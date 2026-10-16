"""Negative selection first: rules that remove names from a displayed list, and cash as an explicit answer.

These filters only take names **away** from what the terminal shows; they never add a name, change a frozen paper
book's rules (ADR-0004: a paper strategy is frozen for its paper period) or size anything up. Each rule carries the
evidence it rests on, at its own tier, so a filter is never mistaken for a tested edge:

    crisis_momentum   no momentum / 52-week-high longs while the regime brain says CRISIS. Result of the regime and
                      decay tests: mom_12_1 IC -0.132 (t -8.4), dist_52w_high -0.204 (t -11.1) in CRISIS sessions.
    falling_liquidity rupee volume (adv20) below 60% of its own one-year average. The drying-up volume shape of
                      Result of published price anomalies; registered out of sample only (PRE-REGISTERED drying-up
                      volume avoid filter) - an avoid flag at observation tier, not a proven edge.
    edge_below_cost   expected net return smaller than the round-trip cost itself (a cover of at least 2x cost
                      before an expectation that is in-sample and uncertain is worth acting on).
    no_edge           expected net at or below zero.

``cash_share`` turns what survives into an explicit cash line: with nothing surviving, the answer is 100% cash.
"""
from __future__ import annotations

import pandas as pd

MOMENTUM_TYPES = {"momentum", "relative_strength", "breakout", "composite"}   # composite leans on 12-1 and 52w-high
LIQUIDITY_FLOOR = 0.60
EDGE_COVER = 1.0              # expected net must exceed this multiple of the cost (net >= cost: gross >= 2x cost)

RULES = {
    "crisis_momentum": "regime CRISIS: momentum reverses (IC t -8.4 / 52w-high t -11.1, regime and decay tests)",
    "falling_liquidity": f"adv20 below {LIQUIDITY_FLOOR:.0%} of its one-year average (avoid shape; registered OOS only)",
    "edge_below_cost": "expected net smaller than the round trip it pays",
    "no_edge": "expected net at or below zero",
}


def liquidity_trend(rcon, as_of, lineages: list[str]) -> pd.Series:
    """adv20 today / its mean over the previous 250 sessions, per lineage."""
    if not lineages:
        return pd.Series(dtype=float)
    df = rcon.execute("""
        WITH w AS (SELECT lineage, business_date, adv20,
                          AVG(adv20) OVER (PARTITION BY lineage ORDER BY business_date
                                           ROWS BETWEEN 250 PRECEDING AND 1 PRECEDING) AS adv_1y
                   FROM features WHERE lineage IN (SELECT UNNEST(?)) AND business_date <= ?
                     AND business_date >= CAST(? AS DATE) - INTERVAL 400 DAY)
        SELECT lineage, adv20 / NULLIF(adv_1y, 0) AS ratio FROM w WHERE business_date = ?""",
                      [lineages, as_of, as_of, as_of]).df()
    return df.set_index("lineage")["ratio"]


def apply(best: pd.DataFrame, *, regime: str | None, liquidity: pd.Series | None = None) -> pd.DataFrame:
    """``best`` with a ``filtered`` column: the first rule each name trips, or '' when it survives."""
    out = best.copy()
    why = []
    for r in out.itertuples():
        types = set(str(getattr(r, "flagged_by", "") or getattr(r, "type", "")).split(","))
        ratio = liquidity.get(getattr(r, "instrument", None)) if liquidity is not None else None
        if r.expected_net <= 0:
            why.append("no_edge")
        elif r.expected_net < EDGE_COVER * r.cost:
            why.append("edge_below_cost")
        elif regime == "CRISIS" and types & MOMENTUM_TYPES:
            why.append("crisis_momentum")
        elif ratio is not None and pd.notna(ratio) and ratio < LIQUIDITY_FLOOR:
            why.append("falling_liquidity")
        else:
            why.append("")
    out["filtered"] = why
    return out


def cash_share(filtered: pd.DataFrame, *, slots: int = 10) -> float:
    """The share of a ``slots``-name book that stays in cash: one slot per name that did not survive."""
    survivors = int((filtered.head(slots)["filtered"] == "").sum())
    return 1.0 - survivors / slots
