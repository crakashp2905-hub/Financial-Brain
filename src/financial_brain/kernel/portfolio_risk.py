"""Portfolio performance and risk measures (FB-0242, FB-0254, FB-0255, FB-0256, FB-0275).

* ``profit_factor``, ``max_drawdown`` and ``time_weighted_return`` measure performance. Profit factor is None when there
  are no losing trades, rather than an infinite or invented value.
* ``marginal_risk_contributions`` splits portfolio volatility into per-position contributions that sum to it.
* ``group_breaches`` checks summed weights per group (sector, country, factor) against a limit.
* ``liquidation_days`` and ``participation_ok`` apply ADV-based participation limits.
* ``historical_var_cvar`` gives VaR and expected shortfall from a return history.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def profit_factor(pnls: Sequence[float]) -> float | None:
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        return None
    return gains / losses


def max_drawdown(equity: Sequence[float]) -> float:
    if not equity:
        raise ValueError("empty equity curve")
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def time_weighted_return(period_returns: Sequence[float]) -> float:
    total = 1.0
    for r in period_returns:
        total *= 1.0 + r
    return total - 1.0


def marginal_risk_contributions(cov: Sequence[Sequence[float]], weights: Sequence[float]) -> list[float]:
    n = len(weights)
    if len(cov) != n or any(len(row) != n for row in cov):
        raise ValueError("covariance must be n by n")
    sigma_w = [sum(cov[i][j] * weights[j] for j in range(n)) for i in range(n)]
    var = sum(weights[i] * sigma_w[i] for i in range(n))
    if var <= 0:
        return [0.0] * n
    vol = math.sqrt(var)
    return [weights[i] * sigma_w[i] / vol for i in range(n)]


def group_breaches(weights: Mapping[str, float], groups: Mapping[str, str], limit: float) -> dict[str, float]:
    totals: dict[str, float] = {}
    for symbol, weight in weights.items():
        group = groups.get(symbol)
        if group is None:
            raise KeyError(f"no group for {symbol}")
        totals[group] = totals.get(group, 0.0) + abs(weight)
    return {g: t for g, t in totals.items() if t > limit}


def liquidation_days(quantity: float, adv: float, participation: float) -> float:
    if adv <= 0 or not (0 < participation <= 1):
        raise ValueError("adv must be positive and participation in (0, 1]")
    return abs(quantity) / (adv * participation)


def participation_ok(quantity: float, adv: float, participation: float, max_days: float) -> bool:
    return liquidation_days(quantity, adv, participation) <= max_days


def historical_var_cvar(returns: Sequence[float], alpha: float) -> tuple[float, float]:
    """VaR and expected shortfall as positive loss numbers at confidence 1-alpha."""
    if not returns:
        raise ValueError("no returns")
    if not (0 < alpha < 1):
        raise ValueError("alpha must be between 0 and 1")
    ordered = sorted(returns)
    cut = max(0, math.ceil(alpha * len(ordered)) - 1)
    var = -ordered[cut]
    tail = [-r for r in ordered[: cut + 1]]
    return var, sum(tail) / len(tail)
