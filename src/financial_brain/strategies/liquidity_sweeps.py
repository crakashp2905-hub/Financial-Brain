"""Causal liquidity-sweep event detector using confirmed levels and closed candles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

from .price_action_gaps import Candle

LevelSide = Literal["HIGH", "LOW"]


@dataclass(frozen=True)
class ConfirmedLevel:
    level_id: str
    side: LevelSide
    price: float
    confirmed_at: datetime


@dataclass(frozen=True)
class LiquiditySweep:
    level_id: str
    side: LevelSide
    level_price: float
    threshold: float
    swept_at: datetime
    reclaimed_at: datetime
    sweep_candle_id: str
    reclaim_candle_id: str
    bars_to_reclaim: int


def detect_liquidity_sweep(
    level: ConfirmedLevel,
    candles: list[Candle],
    *,
    threshold: float,
    max_confirmation_bars: int,
) -> LiquiditySweep | None:
    """Find first breach then a reclaim on a later close, bounded by bar count.

    ``threshold`` is an absolute price distance. A high is swept above level+threshold
    and reclaimed by a later close below the level; lows mirror that rule. Same-candle
    breach/reclaim is rejected because OHLC cannot reveal which happened first.
    """
    if (not level.level_id or level.confirmed_at.tzinfo is None
            or level.side not in ("HIGH", "LOW") or not isfinite(level.price)):
        raise ValueError("level needs an id and timezone-aware confirmation time")
    if not isfinite(threshold) or threshold <= 0 or max_confirmation_bars < 1:
        raise ValueError("threshold and confirmation window must be positive")
    for candle in candles:
        candle.validate()
    if any(left.closed_at >= right.closed_at for left, right in zip(candles, candles[1:])):
        raise ValueError("candles must be strictly ordered by close time")
    eligible = [candle for candle in candles if candle.closed_at > level.confirmed_at]
    sweep_index: int | None = None
    sweep_candle: Candle | None = None
    for index, candle in enumerate(eligible):
        candle.validate()
        breached = (candle.high > level.price + threshold if level.side == "HIGH"
                    else candle.low < level.price - threshold)
        if sweep_index is None:
            if breached:
                sweep_index, sweep_candle = index, candle
            continue
        assert sweep_candle is not None
        bars_to_reclaim = index - sweep_index
        if bars_to_reclaim > max_confirmation_bars:
            return None
        reclaimed = (candle.close < level.price if level.side == "HIGH"
                     else candle.close > level.price)
        if reclaimed:
            return LiquiditySweep(
                level_id=level.level_id, side=level.side, level_price=level.price,
                threshold=threshold, swept_at=sweep_candle.closed_at,
                reclaimed_at=candle.closed_at, sweep_candle_id=sweep_candle.candle_id,
                reclaim_candle_id=candle.candle_id, bars_to_reclaim=bars_to_reclaim)
        if bars_to_reclaim >= max_confirmation_bars:
            return None
    return None
