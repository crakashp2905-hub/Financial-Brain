"""Research diagnostics and cost analysis (FB-0151, FB-0154, FB-0155, FB-0203, FB-0199).

* ``deflated_sharpe`` follows Bailey & Lopez de Prado: the probability the observed Sharpe beats the expected maximum
  Sharpe over ``n_trials`` independent trials. More trials raise the bar.
* ``bootstrap_ci`` gives a seeded percentile interval, so the same seed reproduces the same bounds.
* ``cost_sweep`` and ``breakeven_cost`` work on per-trade gross returns with unit notional: break-even cost is the
  mean gross per trade.
* ``attribute_costs`` splits total cost into named components, and the components must sum to the total.
* ``variation_settlement`` is daily futures mark-to-market: each day's variation is the change in settlement price
  times position times multiplier.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import NormalDist, mean, pstdev

_EULER = 0.5772156649015329


def deflated_sharpe(sr: float, n_obs: int, n_trials: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Probability that the true per-period Sharpe exceeds the expected maximum across ``n_trials`` trials."""
    if n_obs < 2:
        raise ValueError("need at least two observations")
    if n_trials < 1:
        raise ValueError("n_trials must be positive")
    nd = NormalDist()
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = (1 - _EULER) * nd.inv_cdf(1 - 1 / n_trials) + _EULER * nd.inv_cdf(1 - 1 / (n_trials * 2.718281828459045))
    denom_sq = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom_sq <= 0:
        raise ValueError("degenerate higher moments")
    z = (sr - sr0) * (n_obs - 1) ** 0.5 / denom_sq**0.5
    return nd.cdf(z)


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float] = mean,
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    if not values:
        raise ValueError("no values")
    rng = random.Random(seed)
    n = len(values)
    samples = sorted(stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    lo = samples[int((alpha / 2) * (n_boot - 1))]
    hi = samples[int((1 - alpha / 2) * (n_boot - 1))]
    return lo, hi


def cost_sweep(gross_per_trade: Sequence[float], costs: Sequence[float]) -> list[tuple[float, float]]:
    """(cost, net total) for each per-trade cost, unit notional."""
    total = sum(gross_per_trade)
    n = len(gross_per_trade)
    return [(c, total - n * c) for c in costs]


def breakeven_cost(gross_per_trade: Sequence[float]) -> float | None:
    if not gross_per_trade:
        return None
    return sum(gross_per_trade) / len(gross_per_trade)


@dataclass(frozen=True)
class CostComponents:
    spread: Decimal
    impact: Decimal
    fee: Decimal
    delay: Decimal
    opportunity: Decimal

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value < 0:
                raise ValueError(f"{name} cost must be non-negative")

    @property
    def total(self) -> Decimal:
        return self.spread + self.impact + self.fee + self.delay + self.opportunity


def attribute_costs(components: CostComponents, total_reported: Decimal) -> dict[str, Decimal]:
    if components.total != total_reported:
        raise ValueError(f"components sum to {components.total}, reported total is {total_reported}")
    return {
        "spread": components.spread,
        "impact": components.impact,
        "fee": components.fee,
        "delay": components.delay,
        "opportunity": components.opportunity,
        "total": total_reported,
    }


def variation_settlement(prices: Sequence[Decimal], positions: Sequence[int], multiplier: Decimal) -> list[Decimal]:
    """Daily variation for days 1..n-1: (price[t] - price[t-1]) * position[t-1] * multiplier."""
    if len(prices) != len(positions):
        raise ValueError("prices and positions must align")
    out: list[Decimal] = []
    for t in range(1, len(prices)):
        out.append((prices[t] - prices[t - 1]) * Decimal(positions[t - 1]) * multiplier)
    return out


def sharpe_per_period(returns: Sequence[float]) -> float:
    sd = pstdev(returns)
    return 0.0 if sd == 0 else mean(returns) / sd
