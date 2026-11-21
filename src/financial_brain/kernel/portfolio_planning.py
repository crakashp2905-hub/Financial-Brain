"""Portfolio planning tools (FB-0264, FB-0266, FB-0268, FB-0269, FB-0284).

* ``cash_buffer_ok``: free cash must cover margin plus trading costs plus a buffer. Otherwise the plan is refused.
* ``scorecard``: averages supplied factor scores. A missing factor is reported, not silently treated as zero.
* ``apply_shocks``: portfolio percentage change under per-asset shocks. Unshocked assets contribute zero.
* ``rebalance_preview``: trades in units that move current weights to targets at given prices.
* ``RISK_PROFILES``: fixed drawdown and leverage budgets per horizon. Unknown profiles are refused.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

RISK_PROFILES: dict[str, dict[str, Decimal]] = {
    "investor": {"max_drawdown": Decimal("0.20"), "max_leverage": Decimal("1")},
    "swing": {"max_drawdown": Decimal("0.10"), "max_leverage": Decimal("2")},
    "intraday": {"max_drawdown": Decimal("0.03"), "max_leverage": Decimal("4")},
    "derivatives": {"max_drawdown": Decimal("0.15"), "max_leverage": Decimal("3")},
    "research": {"max_drawdown": Decimal("0.05"), "max_leverage": Decimal("1")},
}


def cash_buffer_ok(free_cash: Decimal, margin_need: Decimal, trading_costs: Decimal, buffer_pct: Decimal) -> bool:
    required = margin_need + trading_costs + (margin_need + trading_costs) * buffer_pct
    return free_cash >= required


def scorecard(scores: Mapping[str, float | None], weights: Mapping[str, float]) -> tuple[float | None, list[str]]:
    missing = [k for k in weights if scores.get(k) is None]
    weighted: list[float] = []
    total_w = 0.0
    for k, w in weights.items():
        score = scores.get(k)
        if score is not None:
            weighted.append(score * w)
            total_w += w
    if total_w == 0:
        return None, missing
    return sum(weighted) / total_w, missing


def apply_shocks(weights: Mapping[str, float], shocks: Mapping[str, float]) -> float:
    return sum(w * shocks.get(asset, 0.0) for asset, w in weights.items())


def rebalance_preview(
    current_units: Mapping[str, int],
    target_weights: Mapping[str, float],
    prices: Mapping[str, float],
    equity: float,
) -> dict[str, int]:
    trades: dict[str, int] = {}
    for asset, weight in target_weights.items():
        if asset not in prices or prices[asset] <= 0:
            raise ValueError(f"missing price for {asset}")
        target_units = int(equity * weight / prices[asset])
        trades[asset] = target_units - current_units.get(asset, 0)
    return trades


def get_profile(name: str) -> dict[str, Decimal]:
    if name not in RISK_PROFILES:
        raise KeyError(f"unknown risk profile {name!r}")
    return RISK_PROFILES[name]
