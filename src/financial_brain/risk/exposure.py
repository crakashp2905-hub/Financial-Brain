"""What the book is actually exposed to, as opposed to what it appears to hold.

A list of positions is not an exposure report. Twelve names in twelve different companies can
be one bet on one thing, and the ways that happens in Indian equities are specific:

    the same **promoter group** behind different listed entities
    the same **factor** - all of them recent winners, or all of them cheap
    the same **liquidity tier**, so the whole book is illiquid at the same moment
    the same **direction on the market**, which is beta and is not diversification

The first is already gated per-name in ``decisions/safety.py`` at one position per group. The
rest are aggregate facts no per-name check can see, and they are what this module computes.

Weights come from ``paper_trades.weight`` - the fraction of the book each position was given.
Nothing here decides anything; ``risk/limits.py`` compares these numbers to the limits. A
report that also enforces is a report nobody can read without knowing the thresholds.

## Sector exposure cannot be computed, and that is a data gap rather than an omission

The review this module answers asks for sector and industry limits, and they are the right
limits to want - sector is the single largest source of accidental concentration in an equity
book. **This archive holds no industry classification.** `security_reference` carries
`company_name`, `series`, `listing_date`, face and paid-up value, and no sector field;
`index_constituents`, which would let sector be inferred from index membership, has **zero
rows**.

So `by_industry` is not implemented rather than implemented badly. Guessing sector from a
company's name, or from whatever index it might belong to, would produce a confident number
with no source behind it - which is the failure mode this project spends most of its effort
avoiding. Closing this needs NSE or AMFI sector data ingested and versioned like every other
reference source.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

#: Liquidity tiers, matching the impact buckets in ``costs/india.py``, cheapest first.
TIERS = ("mega", "large", "mid", "small", "micro")


def positions(con, as_of: date | None = None, *, window_days: int | None = None) -> list[dict]:
    """The book, from the paper ledger.

    Default is the **open** book: positions with no exit. ``window_days`` instead returns
    everything entered in that many days up to ``as_of``, open or closed, which is how the
    exposures the system *actually took* get audited after the fact - and with no positions
    currently open, that is the only version with anything in it.
    """
    if window_days is None:
        where, params = "p.status <> 'closed'", []
    else:
        where = ("p.entry_date > CAST(? AS DATE) - INTERVAL (?) DAY "
                 "AND p.entry_date <= CAST(? AS DATE)")
        params = [as_of, window_days, as_of]
    rows = con.execute(f"""
        SELECT p.decision_id, p.isin, p.entry_date, p.entry_price, p.weight, p.bucket,
               p.status, p.excess,
               (SELECT MAX(l.ticker) FROM security_listings l WHERE l.isin = p.isin),
               (SELECT MIN(g.group_id) FROM promoter_groups g WHERE g.member = p.isin),
               (SELECT MIN(g.anchor) FROM promoter_groups g WHERE g.member = p.isin)
        FROM paper_trades p
        WHERE {where}
        ORDER BY p.entry_date
    """, params).fetchall()
    cols = ["decision_id", "isin", "entry_date", "entry_price", "weight", "bucket",
            "status", "excess", "ticker", "group_id", "group_anchor"]
    return [dict(zip(cols, r)) for r in rows]


def _share(book: list[dict], key) -> dict[str, float]:
    total = sum(p["weight"] or 0.0 for p in book)
    if not total:
        return {}
    agg: dict[str, float] = defaultdict(float)
    for p in book:
        agg[key(p) or "UNCLASSIFIED"] += p["weight"] or 0.0
    return {k: v / total for k, v in sorted(agg.items(), key=lambda kv: -kv[1])}


def by_group(book: list[dict]) -> dict[str, float]:
    """Share of the book behind each promoter group.

    `UNCLASSIFIED` is not zero risk, it is *unmeasured* risk, and it is reported under its own
    name rather than dropped - a book that is 60% unclassified has a concentration report that
    means nothing, and the reader should be able to see that.
    """
    return _share(book, lambda p: p["group_anchor"] or p["group_id"])


def by_tier(book: list[dict]) -> dict[str, float]:
    """Share of the book in each liquidity tier, from the tier recorded **with the trade**.

    Point in time by construction: the bucket stored on the position is the one that priced its
    costs on the day it was opened. Re-deriving it from today's ranking would tell you what the
    book would be if formed now, which is not what it is.
    """
    return _share(book, lambda p: p["bucket"])


def factor_tilts(con, book: list[dict]) -> dict[str, float]:
    """The book's mean cross-sectional percentile on each tested factor, **as at entry**.

    0.5 is the universe. 0.9 on `mom_12_1` says the book sat nine-tenths of the way up the
    momentum ranking, which is a factor bet whether or not anyone intended one. Percentiles
    rather than raw values, because the raw units are not comparable across factors and a
    percentile is what a tilt actually means.

    Each position is scored on **its own entry date**, not on a common date: a book assembled
    over three months has no single cross-section, and scoring it against the latest one would
    read later information into earlier entries.
    """
    feats = ["mom_12_1", "dist_52w_high", "vol_60", "ret_20d", "adv20"]
    if not book:
        return {}
    cols = ", ".join(f"PERCENT_RANK() OVER (PARTITION BY f.business_date "
                     f"ORDER BY f.{f}) AS p_{f}" for f in feats)
    want = [(p["isin"], p["entry_date"]) for p in book if p["entry_date"]]
    if not want:
        return {}
    rows = con.execute(f"""
        WITH pairs AS (
            SELECT UNNEST(?::VARCHAR[]) AS isin, UNNEST(?::DATE[]) AS d
        ), at_entry AS (
            SELECT pr.isin, pr.d,
                   (SELECT MAX(f2.business_date) FROM features f2
                    JOIN security_lineage sl ON sl.lineage = f2.lineage
                    WHERE sl.isin = pr.isin AND f2.business_date <= pr.d) AS bd
            FROM pairs pr
        ), ranked AS (
            SELECT f.lineage, f.business_date, {cols}
            FROM features f
            WHERE f.business_date IN (SELECT DISTINCT bd FROM at_entry WHERE bd IS NOT NULL)
        )
        SELECT a.isin, {", ".join(f"r.p_{f}" for f in feats)}
        FROM at_entry a
        JOIN security_lineage sl ON sl.isin = a.isin
        JOIN ranked r ON r.lineage = sl.lineage AND r.business_date = a.bd
    """, [[w[0] for w in want], [w[1] for w in want]]).fetchall()
    if not rows:
        return {}
    out: dict[str, float] = {}
    for i, f in enumerate(feats, start=1):
        vals = [r[i] for r in rows if r[i] is not None]
        if vals:
            out[f] = sum(vals) / len(vals)
    out["_names_scored"] = len(rows)
    return out


def report(con, as_of: date, *, window_days: int | None = None) -> dict:
    """Everything the portfolio gate needs to know about the book as it stands."""
    book = positions(con, as_of, window_days=window_days)
    weight = sum(p["weight"] or 0.0 for p in book)
    groups = by_group(book)
    return {
        "as_of": as_of, "window_days": window_days,
        "positions": len(book), "gross_weight": weight,
        "by_group": groups,
        "largest_group": (max(groups.items(), key=lambda kv: kv[1]) if groups else None),
        "unclassified_group_share": groups.get("UNCLASSIFIED", 0.0),
        "by_tier": by_tier(book),
        # Illiquid is where impact stops being an estimate and starts being a problem.
        "illiquid_share": sum(v for k, v in by_tier(book).items()
                              if k in ("small", "micro")),
        "factor_tilts": factor_tilts(con, book),
        # Stated rather than silently absent - see the module docstring.
        "by_industry": None,
        "industry_available": False,
        "names": [{"isin": p["isin"], "ticker": p["ticker"], "weight": p["weight"],
                   "group": p["group_anchor"], "tier": p["bucket"],
                   "entered": p["entry_date"], "status": p["status"]} for p in book],
    }
