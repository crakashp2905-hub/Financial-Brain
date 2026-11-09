"""Closing the remaining logic gaps of four partial items (FB-0326, FB-0334, FB-0440, FB-0469).

* ``futures_mean_reversion`` is the futures mean-reversion baseline: long when the z-score of the prior window is at
  or below minus the entry threshold, applied from the next bar. Pairs with ``futures_breakout`` in batch 20i.
* ``venue_tick`` returns the tick size for a venue and price band from a published schedule. Unknown venues are refused.
* ``trend_baselines`` returns the Donchian channel trend and the moving-average crossover baseline side by side.
* ``calibration_curve`` gives (mean predicted, observed rate, count) per bin for forecast probabilities.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from financial_brain.kernel.baselines import ma_crossover
from financial_brain.kernel.batch20c_strategies import donchian_signal
from financial_brain.kernel.learning_tools import calibration_bins
from financial_brain.kernel.strategy_primitives import zscore_prior

# venue -> list of (price floor, tick) tiers, ascending by floor
TICK_SCHEDULES: dict[str, list[tuple[Decimal, Decimal]]] = {
    "NSE": [(Decimal("0"), Decimal("0.05")), (Decimal("100"), Decimal("0.10"))],
    "CME": [(Decimal("0"), Decimal("0.25"))],
}


def futures_mean_reversion(values: Sequence[float | None], window: int, entry_z: float) -> list[int]:
    if entry_z <= 0:
        raise ValueError("entry threshold must be positive")
    z = zscore_prior(values, window)
    raw = [1 if (v is not None and v <= -entry_z) else 0 for v in z]
    return [0] + raw[:-1]


def venue_tick(venue: str, price: Decimal) -> Decimal:
    if venue not in TICK_SCHEDULES:
        raise KeyError(f"no tick schedule for venue {venue!r}")
    if price <= 0:
        raise ValueError("price must be positive")
    tick = TICK_SCHEDULES[venue][0][1]
    for floor, size in TICK_SCHEDULES[venue]:
        if price >= floor:
            tick = size
    return tick


def trend_baselines(
    close: Sequence[float], high: Sequence[float], low: Sequence[float], n: int, fast: int, slow: int
) -> Mapping[str, list[int]]:
    return {
        "donchian": donchian_signal(close, high, low, n),
        "ma_crossover": ma_crossover(close, fast, slow),
    }


def calibration_curve(probs: Sequence[float], outcomes: Sequence[int], bins: int) -> list[tuple[float, float, int]]:
    return calibration_bins(probs, outcomes, bins)
