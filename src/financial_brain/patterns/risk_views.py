"""Basket diagnostics and point-in-time controls for filtered pattern candidates (CP-0173, CP-0181, CP-0215, CP-0217).

These are descriptive checks on paper candidates. A missing point-in-time attribute produces ``DATA_INSUFFICIENT``
and never passes a control. Nothing here sizes, places, or approves an order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from financial_brain.patterns.agent import explain_rejection

DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
UNKNOWN_SECTOR = "UNKNOWN"


def sector_concentration(weights: Mapping[str, tuple[str | None, float]]) -> dict[str, Any]:
    """Herfindahl index and largest sector share for a basket (CP-0215).

    ``weights`` maps symbol to ``(sector, absolute weight)``. Symbols with no sector are grouped as
    ``UNKNOWN`` and flagged, because a concentration figure that hides them is not reliable.
    """
    total = sum(abs(w) for _s, w in weights.values())
    if total <= 0:
        return {"status": DATA_INSUFFICIENT, "reason": "empty or zero-weight basket"}
    by_sector: dict[str, float] = {}
    for sector, weight in weights.values():
        key = sector or UNKNOWN_SECTOR
        by_sector[key] = by_sector.get(key, 0.0) + abs(weight) / total
    hhi = sum(share * share for share in by_sector.values())
    largest = max(by_sector, key=lambda k: (by_sector[k], k))
    return {
        "status": DATA_INSUFFICIENT if UNKNOWN_SECTOR in by_sector else "OK",
        "hhi": round(hhi, 6),
        "largest_sector": largest,
        "largest_share": round(by_sector[largest], 6),
        "shares": {k: round(v, 6) for k, v in sorted(by_sector.items())},
    }


def beta_diagnostics(weights: Mapping[str, float], betas: Mapping[str, float | None]) -> dict[str, Any]:
    """Weight-averaged beta for a basket (CP-0215). Any symbol without a beta makes the figure unavailable."""
    total = sum(abs(w) for w in weights.values())
    missing = sorted(s for s in weights if betas.get(s) is None)
    if total <= 0:
        return {"status": DATA_INSUFFICIENT, "reason": "empty or zero-weight basket"}
    if missing:
        return {"status": DATA_INSUFFICIENT, "missing_beta": missing}
    beta = sum(weights[s] * float(betas[s] or 0.0) for s in weights) / total  # missing betas already returned
    return {"status": "OK", "weighted_beta": round(beta, 6)}


def point_in_time_controls(
    candidates: Sequence[Mapping[str, Any]],
    attributes: Mapping[str, Mapping[str, Any]],
    *,
    min_liquidity: float,
    min_size: float,
) -> list[dict[str, Any]]:
    """Liquidity, size and sector presence per candidate, using attributes known at the decision time (CP-0173).

    ``attributes[symbol]`` must already be filtered to the decision date by the caller. A missing field yields
    ``DATA_INSUFFICIENT`` for that control; it is never treated as a pass.
    """
    out: list[dict[str, Any]] = []
    for cand in candidates:
        symbol = str(cand.get("symbol"))
        attr = attributes.get(symbol, {})
        checks: dict[str, str] = {}
        for name, field, floor in (("liquidity", "liquidity", min_liquidity), ("size", "market_cap", min_size)):
            value = attr.get(field)
            if value is None:
                checks[name] = DATA_INSUFFICIENT
            else:
                checks[name] = "PASS" if float(value) >= floor else "FAIL"
        checks["sector"] = "PASS" if attr.get("sector") else DATA_INSUFFICIENT
        out.append({"symbol": symbol, "checks": checks, "eligible": all(v == "PASS" for v in checks.values())})
    return out


def turnover_and_cost_delta(
    unfiltered: Sequence[Mapping[str, float]],
    filtered: Sequence[Mapping[str, float]],
    *,
    cost_rate: float,
    participation: float | None = None,
) -> dict[str, float]:
    """Turnover and cost change attributable to a pattern filter (CP-0181).

    Turnover is the sum of absolute notionals. ``cost_delta`` is the cost the filter saves (positive) or adds
    (negative) at ``cost_rate`` per unit of notional. When ``participation`` is given and each trade carries an
    ``adv`` (average daily volume in the same units), capacity-adjusted turnover caps each trade at
    ``participation * adv`` and reports the matching cost delta. Both sides must be supplied.
    """
    if cost_rate < 0:
        raise ValueError("cost_rate must be non-negative")
    turn_u = sum(abs(float(t["notional"])) for t in unfiltered)
    turn_f = sum(abs(float(t["notional"])) for t in filtered)
    out: dict[str, float] = {
        "turnover_unfiltered": round(turn_u, 6),
        "turnover_filtered": round(turn_f, 6),
        "cost_delta": round(cost_rate * (turn_u - turn_f), 6),
    }
    if participation is not None:
        if not 0 < participation <= 1:
            raise ValueError("participation must be in (0, 1]")

        def capped(rows: Sequence[Mapping[str, float]]) -> float:
            return sum(min(abs(float(t["notional"])), participation * float(t["adv"])) for t in rows)

        cap_u, cap_f = capped(unfiltered), capped(filtered)
        out.update(
            {
                "capacity_adjusted_turnover_unfiltered": round(cap_u, 6),
                "capacity_adjusted_turnover_filtered": round(cap_f, 6),
                "capacity_cost_delta": round(cost_rate * (cap_u - cap_f), 6),
            }
        )
    return out


def candidate_risk_explanations(codes: Sequence[str]) -> list[dict[str, str]]:
    """Plain-language risk-gate explanations for a candidate's rejection codes (CP-0217)."""
    return [{"code": row["code"], "reason": row["reason"]} for row in explain_rejection(list(codes))]
