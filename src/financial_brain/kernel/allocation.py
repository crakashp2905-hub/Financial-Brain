"""Portfolio construction baselines and constraints (FB-0249..0251, FB-0250, FB-0257, FB-0263, FB-0264).

* Weights are fractions of equity; the remainder is explicit cash. Sum of weights must be <= 1.
* Long-only policy rejects any negative weight (unauthorised shorts).
* Baselines: equal weight, inverse volatility and risk parity (equal risk contribution, diagonal approximation).
* A no-trade band suppresses small rebalances so an unchanged book is not churned.
* Drawdown de-risking scales risky weights down once a threshold is crossed; it never increases exposure.
"""
from __future__ import annotations

import math


class AllocationError(ValueError):
    pass


def equal_weight(names: list[str], invested: float = 1.0) -> dict[str, float]:
    if not names:
        raise AllocationError("no assets")
    if not 0 <= invested <= 1:
        raise AllocationError("invested fraction must be between 0 and 1")
    w = invested / len(names)
    return {n: w for n in names}


def inverse_volatility(vols: dict[str, float], invested: float = 1.0) -> dict[str, float]:
    if not vols or any(v <= 0 or not math.isfinite(v) for v in vols.values()):
        raise AllocationError("volatilities must be positive and finite")
    inv = {k: 1.0 / v for k, v in vols.items()}
    total = sum(inv.values())
    return {k: invested * x / total for k, x in inv.items()}


def risk_parity_diagonal(vols: dict[str, float], invested: float = 1.0) -> dict[str, float]:
    """Equal risk contribution when assets are uncorrelated: each contributes w_i * sigma_i = const."""
    return inverse_volatility(vols, invested)


def check_constraints(weights: dict[str, float], *, long_only: bool = True, max_weight: float = 1.0) -> list[str]:
    problems = []
    if sum(weights.values()) > 1.0 + 1e-9:
        problems.append("SUM_EXCEEDS_100_PERCENT")
    for k, w in weights.items():
        if not math.isfinite(w):
            problems.append(f"NON_FINITE:{k}")
        if long_only and w < 0:
            problems.append(f"SHORT_NOT_AUTHORISED:{k}")
        if w > max_weight + 1e-12:
            problems.append(f"MAX_WEIGHT:{k}")
    return problems


def rebalance_trades(current: dict[str, float], target: dict[str, float], band: float = 0.02) -> dict[str, float]:
    """Trade only when |target - current| exceeds the band; otherwise the position is left unchanged."""
    if band < 0:
        raise AllocationError("band must be non-negative")
    trades = {}
    for k in sorted(set(current) | set(target)):
        delta = target.get(k, 0.0) - current.get(k, 0.0)
        if abs(delta) > band:
            trades[k] = delta
    return trades


def drawdown_scale(equity: float, peak: float, threshold: float, floor: float = 0.5) -> float:
    """Scale factor for risky exposure. 1.0 until drawdown exceeds threshold, then reduces (never above 1)."""
    if peak <= 0:
        raise AllocationError("peak must be positive")
    dd = max(0.0, (peak - equity) / peak)
    if dd <= threshold:
        return 1.0
    return max(floor, 1.0 - (dd - threshold) / max(threshold, 1e-9))
