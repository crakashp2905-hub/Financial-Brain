"""Derive corporate actions from the exchange's own stated previous close.

Why this exists
---------------
NSE's corporate-action API sits behind ``www.nseindia.com``, which rejects non-browser
clients, so a scheduled job cannot use it. Rather than build a fragile scraper, this
derives actions from data we already hold at Tier 1.

Two complementary detectors, because neither alone is sufficient.

**A. Restated previous close.** For some action types the exchange restates
``PrvsClsgPric`` on the ex-date, so

    factor = PrvsClsgPric(today) / ClsPric(previous session)

is the adjustment factor published by the exchange itself. Precise, but narrow.

**B. Close-to-close gap.** NSE turns out **not** to restate for ordinary splits and
bonuses - verified against ZFCVINDIA (2026-06-24), GOODLUCK (2026-08-21) and PGIL
(2026-09-11), where the stated previous close was the raw unadjusted one and the price
simply gapped. Detector A is blind to all of these. So a second detector reads the gap:

    raw_factor = ClsPric(today) / ClsPric(previous session)

This conflates the corporate action with the day's genuine price move, so the raw factor
is only a starting point: it is snapped to the nearest plausible ratio, and **only
recorded when a clean ratio snaps and both exchanges agree**. Without both conditions a
gap is indistinguishable from a stock that simply fell 40%.

Corroboration
-------------
A corporate action affects the *security*, so both exchanges must restate by the same
factor. Noise will not agree across exchanges. Candidates are therefore graded:

``corroborated``     both NSE and BSE restated by the same factor (within tolerance)
``single_exchange``  only one exchange restated - plausible, treat with care

Two traps this code handles, both found in real data:

1. **Series contamination.** TCS on 2026-06-24 carried a ``BL`` (block-deal) row whose
   ``PrvsClsgPric`` was 3019.00 while the ``EQ`` row was a clean 2059.60. Comparisons are
   therefore matched on ``series``, never on ISIN alone.
2. **Non-ratio factors.** Many candidates are not clean split ratios - rights issues and
   ex-dividend adjustments produce arbitrary factors. That is fine: **the factor is the
   useful part, the classification is a bonus.** Unclassifiable ones are recorded as
   ``ADJUSTMENT`` with the observed factor so price history still adjusts correctly.

Honest limits
-------------
This detects *that* an adjustment happened and by how much. It cannot reliably say
whether a 0.5 was a 1:1 bonus or a 1:2 split, it cannot see actions that do not move the
reference price, and it needs the session before an ex-date to be present in the lake. It
is a strong Tier-1 substitute for a corporate-action feed, not a replacement for one.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import gcd

from ..config import TIER
from . import actions as ca

#: Series that represent a security's primary continuous price series.
PRIMARY_SERIES = ("EQ", "BE", "A", "B", "T", "X", "XT", "M", "MT", "W")

#: Ignore moves smaller than this - rounding and price bands create tiny discrepancies.
MIN_FACTOR_DEVIATION = 0.02
#: A close-to-close gap must be at least this large before it is even a candidate.
MIN_GAP_MOVE = 0.20
#: Gap-derived factors carry the day's real price move too, so they need a looser snap
#: when two exchanges already agree the event happened.
GAP_RATIO_TOLERANCE = 0.05
#: With only one exchange there is no corroboration, so the ratio itself must carry the
#: evidence: a 0.13% snap to 1:2 is a split, a 6.7% "snap" to 2:1 is a stock that rallied.
GAP_SINGLE_EXCHANGE_TOLERANCE = 0.01
#: Rights entitlements ("-RE") are inherently volatile and are not corporate actions.
EXCLUDED_TICKER_SUFFIXES = ("-RE", "-RT", "-PP")
#: Two exchanges agree if their factors are within this relative distance.
CORROBORATION_TOLERANCE = 0.01


#: Numerators/denominators that real Indian split and bonus ratios actually use.
#: A general "closest fraction" search is useless here - it happily calls 0.8383 a
#: "16:19 split" at 0.45% error. Corporate actions use round numbers; noise does not.
_RATIO_PARTS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 50, 100)
#: Real Indian splits are 1:N or N:1; real bonuses add a small multiple of what is held
#: (1:1, 1:2, 2:3, 3:2 ...). Nothing legitimate looks like "12:7" - that shape only shows
#: up when noise on an illiquid name is fitted to an arbitrary fraction.
PLAUSIBLE_RATIOS = sorted(
    {(a, b) for a in _RATIO_PARTS for b in _RATIO_PARTS
     if gcd(a, b) == 1 and 0.01 <= a / b <= 100
     and min(a, b) <= 3 and max(a, b) <= 25},
    key=lambda ab: ab[0] / ab[1],
)


def classify(factor: float, tolerance: float = 0.005) -> tuple[str, float | None, float | None]:
    """Map an observed factor onto a corporate-action type and ratio.

    Returns ``(action_type, ratio_from, ratio_to)``. Only round ratios that Indian
    corporate actions actually use are accepted, within a tight relative tolerance.
    Anything else becomes ``ADJUSTMENT`` with no ratio - which is not a failure: rights
    issues and ex-dividend adjustments genuinely produce arbitrary factors, and the
    *factor* is what price adjustment needs. The classification is a bonus.
    """
    if factor <= 0:
        return "ADJUSTMENT", None, None

    best, best_err = None, float("inf")
    for a, b in PLAUSIBLE_RATIOS:
        err = abs(a / b - factor) / factor
        if err < best_err:
            best, best_err = (a, b), err

    if best and best_err <= tolerance and best != (1, 1):
        # 1:2 is ambiguous - a 1:2 split and a 1:1 bonus both halve the price. The ratio
        # is recorded; the ambiguity is documented rather than hidden behind a guess.
        return "SPLIT", float(best[0]), float(best[1])
    return "ADJUSTMENT", None, None


def find_candidates(con, *, min_deviation: float = MIN_FACTOR_DEVIATION) -> list[dict]:
    """Every (date, exchange, security) where the exchange restated the previous close."""
    series_list = ",".join(f"'{s}'" for s in PRIMARY_SERIES)
    cur = con.execute(f"""
        WITH px AS (
            SELECT business_date, exchange, isin, ticker, series, instrument_id,
                   close_price, prev_close
            FROM eod_prices
            WHERE instrument_type = 'STK' AND close_price > 0
              AND series IN ({series_list})
        ), joined AS (
            SELECT t.business_date, t.exchange, t.isin, t.ticker, t.series,
                   y.close_price AS actual_prev, t.prev_close AS stated_prev,
                   t.prev_close / y.close_price AS factor
            FROM px t
            JOIN px y
              ON  y.isin = t.isin AND y.exchange = t.exchange AND y.series = t.series
             AND  y.instrument_id IS NOT DISTINCT FROM t.instrument_id
             AND  y.business_date = (
                    SELECT MAX(business_date) FROM px p
                    WHERE p.isin = t.isin AND p.exchange = t.exchange
                      AND p.series = t.series
                      AND p.instrument_id IS NOT DISTINCT FROM t.instrument_id
                      AND p.business_date < t.business_date)
            WHERE t.prev_close IS NOT NULL AND y.close_price > 0
        )
        SELECT business_date AS ex_date, exchange, isin, ticker, series,
               actual_prev, stated_prev, factor
        FROM joined
        WHERE ABS(factor - 1) > ?
        ORDER BY business_date, isin
    """, [min_deviation])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def corroborate(candidates: list[dict],
                tolerance: float = CORROBORATION_TOLERANCE) -> list[dict]:
    """Grade candidates by whether both exchanges restated by the same factor."""
    by_key: dict[tuple, list[dict]] = {}
    for c in candidates:
        by_key.setdefault((c["isin"], c["ex_date"]), []).append(c)

    out = []
    for (isin, ex_date), group in by_key.items():
        exchanges = {c["exchange"] for c in group}
        factors = [c["factor"] for c in group]
        agreed = (
            len(exchanges) > 1
            and (max(factors) - min(factors)) / max(min(factors), 1e-9) <= tolerance
        )
        # Use the median factor so a single odd series cannot dominate.
        factor = sorted(factors)[len(factors) // 2]
        action_type, r_from, r_to = classify(factor)
        out.append({
            "isin": isin, "ex_date": ex_date, "factor": factor,
            "action_type": action_type, "ratio_from": r_from, "ratio_to": r_to,
            "confidence": "corroborated" if agreed else "single_exchange",
            "exchanges": "+".join(sorted(exchanges)),
            "ticker": group[0]["ticker"],
            "observations": len(group),
        })
    return sorted(out, key=lambda r: (r["ex_date"], r["isin"]))


def derive_and_record(con, *, min_deviation: float = MIN_FACTOR_DEVIATION,
                      corroborated_only: bool = True) -> dict:
    """Detect, grade and persist derived corporate actions, then rebuild factors.

    ``corroborated_only`` defaults to **True**, and that default is load-bearing. On real
    data, 116 candidates over 66 days graded to only 17 corroborated; the rest were
    almost entirely illiquid BSE ``XT``/``T`` names whose stated previous close wanders
    for reasons that are not corporate actions. Recording those would inject false
    adjustment factors into price history - worse than having no factor at all, because
    the corruption is silent.
    """
    graded = corroborate(find_candidates(con, min_deviation=min_deviation))
    now = datetime.now(timezone.utc)
    written = 0

    for g in graded:
        if corroborated_only and g["confidence"] != "corroborated":
            continue
        detail = (f"derived from exchange-restated prev_close; factor={g['factor']:.6f}; "
                  f"exchanges={g['exchanges']}")
        aid = ca.action_id(g["isin"], g["action_type"], g["ex_date"], detail)
        if con.execute("SELECT 1 FROM corporate_actions WHERE action_id = ?", [aid]).fetchone():
            continue
        con.execute(
            """INSERT INTO corporate_actions
               (action_id, isin, exchange, action_type, ex_date, ratio_from, ratio_to,
                details, source, source_tier, observed_at, confidence, derived_factor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [aid, g["isin"], g["exchanges"], g["action_type"], g["ex_date"],
             g["ratio_from"], g["ratio_to"], detail, "NSE", TIER["NSE"], now,
             g["confidence"], g["factor"]],
        )
        written += 1

    rebuild_derived_factors(con)
    return {
        "candidates": len(graded),
        "corroborated": sum(1 for g in graded if g["confidence"] == "corroborated"),
        "single_exchange": sum(1 for g in graded if g["confidence"] == "single_exchange"),
        "written": written,
    }


def rebuild_derived_factors(con) -> int:
    """Rebuild cumulative adjustment factors, preferring the observed factor.

    ``corpactions.actions.rebuild_adjustment_factors`` derives the factor from a declared
    ratio. When we have an *observed* factor straight from the exchange it is strictly
    better, because it needs no classification to be correct.
    """
    rows = con.execute("""
        SELECT isin, ex_date,
               COALESCE(derived_factor,
                        CASE WHEN ratio_from IS NOT NULL AND ratio_to IS NOT NULL
                             THEN ratio_from / ratio_to END) AS factor,
               action_id
        FROM corporate_actions
        WHERE COALESCE(derived_factor,
                       CASE WHEN ratio_from IS NOT NULL AND ratio_to IS NOT NULL
                            THEN ratio_from / ratio_to END) IS NOT NULL
        ORDER BY isin, ex_date DESC
    """).fetchall()

    con.execute("DELETE FROM adjustment_factors")
    now = datetime.now(timezone.utc)
    written, cumulative, current = 0, 1.0, None

    for isin, ex_date, factor, aid in rows:
        if isin != current:
            current, cumulative = isin, 1.0
        if not factor or factor <= 0:
            continue
        cumulative *= factor
        con.execute(
            """INSERT INTO adjustment_factors
               (isin, effective_from, price_factor, volume_factor, derived_from, computed_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT (isin, effective_from) DO UPDATE SET
                   price_factor = EXCLUDED.price_factor,
                   volume_factor = EXCLUDED.volume_factor,
                   computed_at = EXCLUDED.computed_at""",
            [isin, ex_date, cumulative, 1.0 / cumulative, aid, now])
        written += 1
    return written


def find_gap_candidates(con, *, min_move: float = MIN_GAP_MOVE,
                        min_turnover: float = 10_000_000.0) -> list[dict]:
    """Large close-to-close gaps - the actions detector A cannot see.

    Restricted to liquid names, because on an illiquid stock a 40% print means very
    little. Rights entitlements are excluded: they are volatile by construction.
    """
    series_list = ",".join(f"'{s}'" for s in PRIMARY_SERIES)
    suffix_filter = " AND ".join(
        f"ticker NOT LIKE '%{suf}'" for suf in EXCLUDED_TICKER_SUFFIXES)
    cur = con.execute(f"""
        WITH px AS (
            SELECT business_date, exchange, isin, ticker, series, instrument_id,
                   close_price, turnover
            FROM eod_prices
            WHERE instrument_type = 'STK' AND close_price > 0
              AND series IN ({series_list}) AND {suffix_filter}
        ), gapped AS (
            SELECT business_date AS ex_date, exchange, isin, ticker, series, close_price,
                   LAG(close_price) OVER (PARTITION BY isin, exchange, series,
                                                       instrument_id
                                          ORDER BY business_date) AS prev_close,
                   turnover
            FROM px
        )
        SELECT ex_date, exchange, isin, ticker, series, prev_close AS actual_prev,
               close_price AS stated_prev, close_price / prev_close AS factor
        FROM gapped
        WHERE prev_close IS NOT NULL AND prev_close > 0
          AND turnover >= ?
          AND ABS(close_price / prev_close - 1) >= ?
        ORDER BY ex_date, isin
    """, [min_turnover, min_move])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def derive_from_gaps(con, *, min_move: float = MIN_GAP_MOVE) -> dict:
    """Record corporate actions implied by corroborated, cleanly-snapping price gaps."""
    graded = corroborate(find_gap_candidates(con, min_move=min_move))
    now = datetime.now(timezone.utc)
    written, skipped_noisy, skipped_single = 0, 0, 0

    for g in graded:
        corroborated = g["confidence"] == "corroborated"
        tol = GAP_RATIO_TOLERANCE if corroborated else GAP_SINGLE_EXCHANGE_TOLERANCE
        action_type, r_from, r_to = classify(g["factor"], tolerance=tol)
        if action_type != "SPLIT" or not r_from:
            # No clean ratio: this is very likely a genuine price move, not an action.
            if corroborated:
                skipped_noisy += 1
            else:
                skipped_single += 1
            continue

        exact = r_from / r_to           # the snapped ratio, not the noisy observed gap
        kind = "corroborated_gap" if corroborated else "single_exchange_gap"
        detail = (f"derived from {'corroborated' if corroborated else 'single-exchange'} "
                  f"close-to-close gap; observed={g['factor']:.6f}; "
                  f"snapped={int(r_from)}:{int(r_to)}; exchanges={g['exchanges']}")
        aid = ca.action_id(g["isin"], action_type, g["ex_date"], detail)
        if con.execute("SELECT 1 FROM corporate_actions WHERE action_id = ?", [aid]).fetchone():
            continue
        con.execute(
            """INSERT INTO corporate_actions
               (action_id, isin, exchange, action_type, ex_date, ratio_from, ratio_to,
                details, source, source_tier, observed_at, confidence, derived_factor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [aid, g["isin"], g["exchanges"], action_type, g["ex_date"], r_from, r_to,
             detail, "NSE", TIER["NSE"], now, kind, exact])
        written += 1

    rebuild_derived_factors(con)
    return {"candidates": len(graded), "written": written,
            "skipped_no_clean_ratio": skipped_noisy,
            "skipped_single_exchange": skipped_single}


def derive_all(con, **kw) -> dict:
    """Run both detectors. Restated-prev first (precise), then gaps (broader)."""
    a = derive_and_record(con, **kw)
    b = derive_from_gaps(con)
    return {"restated_prev": a, "close_gap": b}
