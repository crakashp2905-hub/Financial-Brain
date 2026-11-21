"""Portfolio construction and monitoring (FB-0252, FB-0253, FB-0258, FB-0259, FB-0260, FB-0261, FB-0265).

* ``min_variance_weight`` gives the long-only minimum-variance weight of asset 1 in a two-asset portfolio.
* ``shrink_covariance`` blends a covariance matrix toward its diagonal; ``is_psd_2x2`` checks a 2x2 result.
* ``POLICY_TEMPLATES`` are fixed policies by horizon; an unknown horizon is refused.
* ``dollar_neutral`` checks long and short gross exposure match within a tolerance.
* ``trade_proposals`` ranks alternative target allocations by one-way turnover.
* ``tracking_error`` is the standard deviation of portfolio minus benchmark returns.
* ``total_return`` adds a cash dividend to price change over one period.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import pstdev


def min_variance_weight(var1: float, var2: float, cov: float) -> float:
    denom = var1 + var2 - 2 * cov
    if denom <= 0:
        raise ValueError("degenerate covariance")
    w = (var2 - cov) / denom
    return min(1.0, max(0.0, w))


def shrink_covariance(cov: Sequence[Sequence[float]], lam: float) -> list[list[float]]:
    if not (0.0 <= lam <= 1.0):
        raise ValueError("shrinkage must be in [0, 1]")
    n = len(cov)
    return [[(1 - lam) * cov[i][j] + (lam * cov[i][j] if i == j else 0.0) for j in range(n)] for i in range(n)]


def is_psd_2x2(m: Sequence[Sequence[float]]) -> bool:
    return m[0][0] >= 0 and m[1][1] >= 0 and m[0][0] * m[1][1] - m[0][1] * m[1][0] >= 0


POLICY_TEMPLATES: dict[str, dict[str, Decimal]] = {
    "short": {"max_weight": Decimal("0.25"), "max_drawdown": Decimal("0.05"), "rebalance_days": Decimal("5")},
    "medium": {"max_weight": Decimal("0.35"), "max_drawdown": Decimal("0.15"), "rebalance_days": Decimal("21")},
    "long": {"max_weight": Decimal("0.50"), "max_drawdown": Decimal("0.30"), "rebalance_days": Decimal("63")},
}


def policy_template(horizon: str) -> dict[str, Decimal]:
    if horizon not in POLICY_TEMPLATES:
        raise KeyError(f"unknown horizon {horizon!r}")
    return POLICY_TEMPLATES[horizon]


def dollar_neutral(long_notional: Decimal, short_notional: Decimal, tolerance: Decimal) -> bool:
    return abs(long_notional - abs(short_notional)) <= tolerance


def trade_proposals(
    current: Mapping[str, float], alternatives: Mapping[str, Mapping[str, float]]
) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    for name, target in alternatives.items():
        assets = set(current) | set(target)
        turnover = sum(abs(target.get(a, 0.0) - current.get(a, 0.0)) for a in assets) / 2
        ranked.append((name, turnover))
    return sorted(ranked, key=lambda item: (item[1], item[0]))


def tracking_error(portfolio: Sequence[float], benchmark: Sequence[float]) -> float:
    if len(portfolio) != len(benchmark) or len(portfolio) < 2:
        raise ValueError("series must align and have at least two points")
    return pstdev([p - b for p, b in zip(portfolio, benchmark, strict=True)])


def total_return(p0: Decimal, p1: Decimal, dividend: Decimal) -> Decimal:
    if p0 <= 0:
        raise ValueError("start price must be positive")
    return (p1 + dividend) / p0 - Decimal(1)
