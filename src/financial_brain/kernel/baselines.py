"""Baseline strategies (FB-0436, FB-0437, FB-0443, FB-0453).

Each baseline maps closes to positions. A position decided from bar ``i`` applies from bar ``i+1``: no same-bar
entry is ever possible. The abstention baseline emits no trades and is the honest opportunity-cost reference.
"""
from __future__ import annotations

from collections.abc import Sequence

from financial_brain.kernel.indicators import sma


def buy_and_hold(closes: Sequence[float]) -> list[int]:
    return [1] * len(closes)


def cash(closes: Sequence[float]) -> list[int]:
    return [0] * len(closes)


def time_series_momentum(closes: Sequence[float], lookback: int) -> list[int]:
    """Long when the close is above its close ``lookback`` bars ago; the decision uses bar i only, applied at i+1."""
    if lookback < 1:
        raise ValueError("lookback must be positive")
    raw: list[int] = [0] * len(closes)
    for i in range(lookback, len(closes)):
        raw[i] = 1 if closes[i] > closes[i - lookback] else 0
    return [0] + raw[:-1]  # shift one bar: no same-bar exposure


def ma_crossover(closes: Sequence[float], fast: int, slow: int) -> list[int]:
    if fast >= slow:
        raise ValueError("fast window must be shorter than slow window")
    f, s = sma(closes, fast), sma(closes, slow)
    raw = [0] * len(closes)
    for i in range(len(closes)):
        fi, si = f[i], s[i]
        if fi is not None and si is not None:
            raw[i] = 1 if fi > si else 0
    return [0] + raw[:-1]


def abstain(_closes: Sequence[float]) -> list[int]:
    """No trades ever. Used as the opportunity-cost reference for every strategy."""
    return [0] * len(_closes)


def strategy_return(closes: Sequence[float], positions: Sequence[int]) -> float:
    total = 1.0
    for i in range(1, len(closes)):
        if positions[i]:
            total *= closes[i] / closes[i - 1]
    return total - 1.0
