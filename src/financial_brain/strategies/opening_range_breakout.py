"""Point-in-time 5/15-minute opening-range breakout setup detector.

This deliberately returns research events, not orders or profitability claims. Bars are
one-minute candles timestamped at close; the opening range must be complete and contiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Literal


@dataclass(frozen=True)
class MinuteBar:
    candle_id: str
    closed_at: datetime
    high: float
    low: float
    close: float
    volume: float

    def validate(self) -> None:
        values = (self.high, self.low, self.close, self.volume)
        if not self.candle_id or self.closed_at.tzinfo is None:
            raise ValueError("bars require an id and timezone-aware close time")
        if not all(isfinite(value) for value in values) or self.volume < 0:
            raise ValueError("bar prices and nonnegative volume must be finite")
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("bar prices are inconsistent")


@dataclass(frozen=True)
class OpeningRangeDecision:
    status: Literal["WAITING", "ACTIVE", "INVALIDATED"]
    minutes: int
    range_high: float
    range_low: float
    range_complete_at: datetime
    side: Literal["LONG", "SHORT"] | None
    signal_at: datetime | None
    invalidated_at: datetime | None
    volume_baseline: float


def evaluate_opening_range(
    bars: list[MinuteBar],
    *,
    session_open: datetime,
    minutes: int,
    buffer: float,
    volume_multiplier: float,
) -> OpeningRangeDecision | None:
    """Evaluate a close-through breakout and close-back-through invalidation.

    The range consists of exactly ``minutes`` consecutive one-minute bars ending at
    session_open + minutes. Longs require a close above range high + buffer and volume
    at least mean range volume times ``volume_multiplier``; shorts mirror that rule.
    """
    if session_open.tzinfo is None:
        raise ValueError("session open must be timezone-aware")
    if minutes not in (5, 15) or not isfinite(buffer) or buffer < 0:
        raise ValueError("only 5/15-minute ranges and a finite nonnegative buffer are supported")
    if not isfinite(volume_multiplier) or volume_multiplier <= 0:
        raise ValueError("volume multiplier must be finite and positive")
    for bar in bars:
        bar.validate()
    if any(left.closed_at >= right.closed_at for left, right in zip(bars, bars[1:])):
        raise ValueError("bars must be strictly ordered by close time")
    range_end = session_open + timedelta(minutes=minutes)
    range_bars = [bar for bar in bars if session_open < bar.closed_at <= range_end]
    expected_closes = [session_open + timedelta(minutes=i) for i in range(1, minutes + 1)]
    if [bar.closed_at for bar in range_bars] != expected_closes:
        return None
    high = max(bar.high for bar in range_bars)
    low = min(bar.low for bar in range_bars)
    baseline = sum(bar.volume for bar in range_bars) / minutes
    if baseline <= 0:
        return None
    threshold_volume = baseline * volume_multiplier
    later = [bar for bar in bars if bar.closed_at > range_end]
    side: Literal["LONG", "SHORT"] | None = None
    signal_at: datetime | None = None
    invalidated_at: datetime | None = None
    for bar in later:
        if side is None:
            if bar.close > high + buffer and bar.volume >= threshold_volume:
                side, signal_at = "LONG", bar.closed_at
            elif bar.close < low - buffer and bar.volume >= threshold_volume:
                side, signal_at = "SHORT", bar.closed_at
        elif (side == "LONG" and bar.close <= high + buffer
              or side == "SHORT" and bar.close >= low - buffer):
            invalidated_at = bar.closed_at
            break
    status: Literal["WAITING", "ACTIVE", "INVALIDATED"]
    status = "WAITING" if side is None else "INVALIDATED" if invalidated_at else "ACTIVE"
    return OpeningRangeDecision(status, minutes, high, low, range_end, side, signal_at,
                                invalidated_at, baseline)
