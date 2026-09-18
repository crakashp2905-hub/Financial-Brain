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
#: The open must already reflect the gap: |(open/prev) / (close/prev) - 1| at most this.
OPEN_GAP_TOLERANCE = 0.08
#: No action is inferred on a day Nifty 50 moved at least this much.
MARKET_SHOCK = 0.05
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


#: Each exchange's previous session, taken from the very rows being compared.
#:
#: A price comparison is only meaningful between consecutive sessions. Partitioning by
#: series is not enough: when a stock is moved EQ -> BE (surveillance reviews move them
#: in batches), its first BE row's LAG is whatever BE printed months earlier, while the
#: exchange's stated previous close is yesterday's EQ close. Over 2015-2026 that made
#: clusters of 30-75 distressed small caps (RELCAPITAL, ANSALHSG, ...) look like
#: corporate actions on review dates. Comparing only when the instrument's previous row
#: *is* the exchange's previous session removes them.
_CALENDAR_CTE = """cal AS (
            SELECT exchange, business_date,
                   LAG(business_date) OVER (PARTITION BY exchange
                                            ORDER BY business_date) AS prev_session
            FROM (SELECT DISTINCT exchange, business_date FROM px))"""


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
    cal, consecutive_note = _CALENDAR_CTE, "consecutive sessions only"
    cur = con.execute(f"""
        WITH px AS (
            SELECT business_date, exchange, isin, ticker, series, instrument_id,
                   close_price, prev_close
            FROM eod_prices
            WHERE instrument_type = 'STK' AND close_price > 0
              AND series IN ({series_list})
        ), {cal}, lagged AS (
            -- The previous *traded* row for the same instrument, in one window pass.
            -- This was a correlated MAX(business_date) subquery: fine on three months,
            -- but over 11 years it ran 16+ CPU-minutes and 8.5 GB without finishing.
            SELECT business_date, exchange, isin, ticker, series, prev_close,
                   LAG(close_price) OVER w AS actual_prev,
                   LAG(business_date) OVER w AS prev_date
            FROM px
            WINDOW w AS (PARTITION BY isin, exchange, series, instrument_id
                         ORDER BY business_date)
        ), joined AS (
            SELECT l.business_date, l.exchange, l.isin, l.ticker, l.series,
                   l.actual_prev, l.prev_close AS stated_prev,
                   l.prev_close / l.actual_prev AS factor
            FROM lagged l
            JOIN cal c ON c.exchange = l.exchange AND c.business_date = l.business_date
            WHERE l.prev_close IS NOT NULL AND l.actual_prev > 0
              AND l.prev_date = c.prev_session   -- {consecutive_note}
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

    Three guards added once the history reached eleven years, where large genuine moves
    started snapping to clean ratios (Adani Enterprises' 2023-02-01 selloff as "3:4",
    RCOM's +36% day as "4:3", eight banks on the COVID crash day as "3:4"):

    * **The gap must be at the open.** An action is applied before the session opens,
      so the stock *opens* at the adjusted price: Wipro's 2019 bonus opened at 0.755x
      and closed at 0.763x the previous close. Adani opened at 1.007x and fell during
      the day. Open/prev must be within ``OPEN_GAP_TOLERANCE`` of close/prev.
    * **The price must fall.** Splits and bonuses only ever lower it; a consolidation
      must be sourced, not inferred.
    * **Not on a market-shock day.** When Nifty 50 moves ``MARKET_SHOCK`` or more, many
      stocks gap at the open together (2020-03-23: -13%, circuit breaker at the open),
      and no action is inferred.
    """
    series_list = ",".join(f"'{s}'" for s in PRIMARY_SERIES)
    suffix_filter = " AND ".join(
        f"ticker NOT LIKE '%{suf}'" for suf in EXCLUDED_TICKER_SUFFIXES)
    cur = con.execute(f"""
        WITH px AS (
            SELECT business_date, exchange, isin, ticker, series, instrument_id,
                   open_price, close_price, turnover
            FROM eod_prices
            WHERE instrument_type = 'STK' AND close_price > 0
              AND series IN ({series_list}) AND {suffix_filter}
        ), {_CALENDAR_CTE}, mkt AS (
            SELECT business_date,
                   close_level / LAG(close_level) OVER (ORDER BY business_date) - 1 AS ret
            FROM index_levels WHERE index_name = 'Nifty 50' AND variant = 'PRICE'
        ), gapped AS (
            SELECT business_date AS ex_date, exchange, isin, ticker, series, close_price,
                   open_price,
                   LAG(close_price) OVER w AS prev_close,
                   LAG(business_date) OVER w AS prev_date,
                   turnover
            FROM px
            WINDOW w AS (PARTITION BY isin, exchange, series, instrument_id
                         ORDER BY business_date)
        )
        SELECT g.ex_date, g.exchange, g.isin, g.ticker, g.series, g.prev_close AS actual_prev,
               g.close_price AS stated_prev, g.close_price / g.prev_close AS factor
        FROM gapped g
        JOIN cal c ON c.exchange = g.exchange AND c.business_date = g.ex_date
        LEFT JOIN mkt m ON m.business_date = g.ex_date
        WHERE g.prev_close IS NOT NULL AND g.prev_close > 0
          AND g.prev_date = c.prev_session
          AND g.close_price < g.prev_close
          AND g.open_price > 0
          AND ABS((g.open_price / g.prev_close) / (g.close_price / g.prev_close) - 1)
              <= {OPEN_GAP_TOLERANCE}
          AND COALESCE(ABS(m.ret), 0) < {MARKET_SHOCK}
          AND g.turnover >= ?
          AND ABS(g.close_price / g.prev_close - 1) >= ?
        ORDER BY g.ex_date, g.isin
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


def clear_derived(con) -> int:
    """Delete every *derived* corporate action, ahead of a full re-derivation.

    Derived actions are a pure function of the price history, not observations of a
    source, so rebuilding them is legitimate - and necessary when the detector is
    corrected: derivation only appends, so ~5,000 phantom actions from missing weekend
    sessions would otherwise sit beside the corrected set forever. Rows with no
    ``derived_factor`` (recorded from an announcement or feed) are never touched.
    """
    n = con.execute(
        "SELECT COUNT(*) FROM corporate_actions WHERE derived_factor IS NOT NULL").fetchone()[0]
    con.execute("DELETE FROM corporate_actions WHERE derived_factor IS NOT NULL")
    return n


#: A price ratio within this of 1 across an ISIN change is an identity-only change
#: (scheme restructure, re-issue) - the security continues, nothing is adjusted.
IDENTITY_ONLY_BAND = 0.10


def derive_from_successions(con) -> dict:
    """Record splits evidenced by an ISIN succession whose price ratio snaps cleanly.

    The succession itself proves an event happened (the exchange published a new ISIN
    for the same scrip code or ticker), so the only open question is the ratio - which
    is why the corroborated gap tolerance applies whether one exchange or both show the
    switch. Confidence is recorded separately. Actions are attached to the new ISIN on
    the first session under it; the ``isin_successions`` row links the old history.
    """
    from ..securities import succession
    counts = succession.record(con)
    now = datetime.now(timezone.utc)
    written = identity_only = no_ratio = duplicate = 0

    for old, new, eff, exchanges, ratio, conf in con.execute(
            """SELECT old_isin, new_isin, effective_date, exchanges, price_ratio, confidence
               FROM isin_successions ORDER BY effective_date""").fetchall():
        if ratio is None:
            no_ratio += 1
            continue
        if abs(ratio - 1) < IDENTITY_ONLY_BAND:
            identity_only += 1
            continue
        action_type, r_from, r_to = classify(ratio, tolerance=GAP_RATIO_TOLERANCE)
        if action_type != "SPLIT" or not r_from:
            no_ratio += 1
            continue
        if con.execute(
                """SELECT 1 FROM corporate_actions WHERE isin IN (?, ?)
                   AND ex_date BETWEEN CAST(? AS DATE) - 3 AND CAST(? AS DATE) + 3""",
                [old, new, eff, eff]).fetchone():
            duplicate += 1
            continue
        detail = (f"derived from ISIN succession {old} -> {new}; observed={ratio:.6f}; "
                  f"snapped={int(r_from)}:{int(r_to)}; exchanges={exchanges}")
        aid = ca.action_id(new, action_type, eff, detail)
        con.execute(
            """INSERT INTO corporate_actions
               (action_id, isin, exchange, action_type, ex_date, ratio_from, ratio_to,
                details, source, source_tier, observed_at, confidence, derived_factor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [aid, new, exchanges, action_type, eff, r_from, r_to, detail, "NSE",
             TIER["NSE"], now, f"{conf}_succession", r_from / r_to])
        written += 1

    rebuild_derived_factors(con)
    return {**counts, "written": written, "identity_only": identity_only,
            "no_clean_ratio": no_ratio, "already_recorded": duplicate}


def derive_all(con, **kw) -> dict:
    """Run every detector: restated prev close, close-to-close gaps, ISIN successions."""
    a = derive_and_record(con, **kw)
    b = derive_from_gaps(con)
    c = derive_from_successions(con)
    return {"restated_prev": a, "close_gap": b, "succession": c}


def _close_of(con, isin: str, exchange: str, d) -> float | None:
    r = con.execute("""SELECT MAX(close_price) FROM universe_snapshots
                       WHERE isin = ? AND exchange = ? AND business_date = ?""",
                    [isin, exchange, d]).fetchone()
    return r[0] if r else None


def find_untriaged_gaps(con, *, threshold: float = 0.35,
                        min_turnover: float = 10_000_000.0) -> list[dict]:
    """Large moves between consecutive sessions with no recorded action or review.

    The single definition of "an unexamined gap", used by triage, by `fb gaps` and by
    the Phase 0 gate. It used to exist twice; the copy the gate used never gained the
    consecutive-session rule and reported 2,201 "untriaged gaps" that were comparisons
    of illiquid stocks against closes weeks or months old.
    """
    suffix_filter = " AND ".join(
        f"ticker NOT LIKE '%{suf}'" for suf in EXCLUDED_TICKER_SUFFIXES)
    cur = con.execute(f"""
        WITH px AS (
            SELECT isin, ticker, exchange, series, business_date, close_price, turnover
            FROM universe_snapshots
            WHERE instrument_type = 'STK' AND close_price > 0 AND {suffix_filter}
        ), {_CALENDAR_CTE}, d AS (
            SELECT x.*, LAG(close_price) OVER w AS prev, LAG(business_date) OVER w AS prev_date
            FROM px x
            WINDOW w AS (PARTITION BY isin, exchange, series ORDER BY business_date))
        SELECT d.business_date AS ex_date, d.exchange, d.ticker, d.isin, d.prev,
               d.close_price, d.close_price / d.prev AS factor
        FROM d JOIN cal c ON c.exchange = d.exchange AND c.business_date = d.business_date
        WHERE d.prev IS NOT NULL AND d.prev_date = c.prev_session AND d.turnover >= ?
          AND ABS(d.close_price / d.prev - 1) >= ?
          AND NOT EXISTS (SELECT 1 FROM corporate_actions ca
                          WHERE ca.isin = d.isin AND ca.ex_date = d.business_date)
          AND NOT EXISTS (SELECT 1 FROM gap_reviews gr
                          WHERE gr.isin = d.isin AND gr.ex_date = d.business_date)
        ORDER BY d.business_date
    """, [min_turnover, threshold])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def auto_triage_gaps(con, *, threshold: float = 0.35,
                     min_turnover: float = 10_000_000.0, redo: bool = False) -> dict:
    """Apply the documented triage rule to every untriaged gap.

    Hand-reviewing seven gaps over three months was reasonable. Over eleven years it is
    not, and rubber-stamping hundreds would be worse than not reviewing them. So the
    reasoning used on the original seven is encoded instead:

    1. **Cross-listed but only one exchange gapped** -> ``price_move``. A corporate
       action affects the security, so it must move both listings. One-sided is positive
       evidence that it is *not* an action - the strongest verdict available here.
    2. **Single-listed** -> ``needs_source``. Corroboration is structurally impossible
       and the ratio did not snap tightly, so it cannot be resolved without an
       authoritative corporate-action feed. Undecidable, and recorded as such.
    3. **Both exchanges gapped but no clean ratio** -> ``needs_source``. Action-like, but
       the ratio is not one a real action uses.

    Nothing here guesses. Verdict 1 is a finding; verdicts 2 and 3 record precisely why
    the question stays open, which keeps the residual dependency visible.

    Two further findings run first, learned from the eleven-year history, where large
    genuine moves (Adani Enterprises 2023-02-01, the COVID crash 2020-03-23) otherwise
    fall through to ``needs_source``:

    0a. **Market-wide shock** (Nifty 50 moved >= ``MARKET_SHOCK``) -> ``price_move``.
    0b. **Moved during the session** (opened within ``OPEN_GAP_TOLERANCE`` of the previous
        close) -> ``price_move``. Corporate actions apply before the open; a stock that
        opened flat and then moved 30% was not split.
    """
    if redo:
        # Automatic verdicts are re-derivable; a person's verdict never is.
        con.execute("DELETE FROM gap_reviews WHERE reviewed_by = 'auto'")

    suffix_filter = " AND ".join(
        f"ticker NOT LIKE '%{suf}'" for suf in EXCLUDED_TICKER_SUFFIXES)
    rows = [(g["ex_date"], g["exchange"], g["ticker"], g["isin"], g["factor"])
            for g in find_untriaged_gaps(con, threshold=threshold,
                                         min_turnover=min_turnover)]

    # Collapse to one decision per (isin, date) - a gap on two exchanges is one event.
    events: dict[tuple, list] = {}
    for business_date, exchange, ticker, isin, factor in rows:
        events.setdefault((isin, business_date), []).append((exchange, ticker, factor))

    # Open prices for every candidate in one pass over the curated prices.
    con.execute("CREATE OR REPLACE TEMP TABLE _gap_cand (isin VARCHAR, exchange VARCHAR, "
                "business_date DATE)")
    if rows:
        con.executemany("INSERT INTO _gap_cand VALUES (?,?,?)",
                        [[r[3], r[1], r[0]] for r in rows])
    opens = {(i, e, d): o for i, e, d, o in con.execute("""
        SELECT e.isin, e.exchange, e.business_date, MAX(e.open_price)
        FROM eod_prices e JOIN _gap_cand c USING (isin, exchange, business_date)
        WHERE e.open_price > 0 GROUP BY 1, 2, 3""").fetchall()}
    shock = dict(con.execute("""
        SELECT business_date, close_level / LAG(close_level) OVER (ORDER BY business_date) - 1
        FROM index_levels WHERE index_name = 'Nifty 50' AND variant = 'PRICE'""").fetchall())

    now = datetime.now(timezone.utc)
    counts = {"action_recorded": 0, "price_move": 0, "needs_source": 0}

    for (isin, ex_date), obs in events.items():
        gapped = {e for e, _, _ in obs}
        listed = {r[0] for r in con.execute(
            """SELECT DISTINCT exchange FROM universe_snapshots
               WHERE isin = ? AND business_date = ?""", [isin, ex_date]).fetchall()}
        factor = sorted(f for _, _, f in obs)[len(obs) // 2]
        ticker = obs[0][1]
        open_ratio = None                     # open / previous close
        for e, _, f in obs:
            o, c = opens.get((isin, e, ex_date)), _close_of(con, isin, e, ex_date)
            if o and c:
                open_ratio = o / (c / f)          # previous close = close / factor
                break
        mkt = shock.get(ex_date)
        succ = con.execute(
            """SELECT s.old_isin, s.new_isin, s.effective_date, ca.ratio_from, ca.ratio_to
               FROM isin_successions s
               JOIN corporate_actions ca ON ca.isin = s.new_isin
                    AND ca.ex_date = s.effective_date
               WHERE ? IN (s.old_isin, s.new_isin)
                 AND s.effective_date BETWEEN CAST(? AS DATE) - 5 AND CAST(? AS DATE) + 5
               LIMIT 1""", [isin, ex_date, ex_date]).fetchone()

        if succ:
            verdict = "action_recorded"
            note = (f"{ticker} factor={factor:.4f}; explained by ISIN succession "
                    f"{succ[0]} -> {succ[1]} on {succ[2]}, recorded as "
                    f"{int(succ[3])}:{int(succ[4])} (a split changes the ISIN, so one "
                    f"exchange showed the gap while the other already had the new ISIN)")
        elif mkt is not None and abs(mkt) >= MARKET_SHOCK:
            verdict = "price_move"
            note = (f"{ticker} factor={factor:.4f} on a market-wide shock day "
                    f"(Nifty 50 {mkt:+.1%}); no action is inferred from such days")
        elif open_ratio is not None and abs(open_ratio - 1) <= OPEN_GAP_TOLERANCE:
            verdict = "price_move"
            note = (f"{ticker} opened at {open_ratio:.3f}x and closed at {factor:.3f}x the "
                    f"previous close - it moved during the session; corporate actions "
                    f"apply before the open")
        elif len(listed) > 1 and len(gapped) == 1:
            verdict = "price_move"
            note = (f"{ticker} gapped on {sorted(gapped)[0]} only while also listed on "
                    f"{sorted(listed - gapped)[0]}; a corporate action would move both")
        elif len(listed) == 1:
            verdict = "needs_source"
            note = (f"{ticker} factor={factor:.4f}; single-listed on {sorted(listed)[0]}, "
                    f"so corroboration is impossible and no clean ratio snapped")
        else:
            verdict = "needs_source"
            note = (f"{ticker} factor={factor:.4f}; both exchanges gapped but the ratio "
                    f"is not one a real corporate action uses")

        con.execute(
            """INSERT INTO gap_reviews
               (isin, ex_date, exchange, observed_factor, verdict, note, reviewed_at,
                reviewed_by)
               VALUES (?,?,?,?,?,?,?,'auto')
               ON CONFLICT (isin, ex_date) DO NOTHING""",
            [isin, ex_date, "+".join(sorted(gapped)), factor, verdict, note, now])
        counts[verdict] += 1

    return {"events": len(events), **counts}
