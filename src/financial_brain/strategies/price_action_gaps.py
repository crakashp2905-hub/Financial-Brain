"""Causal three-candle fair-value-gap detector for research and paper replay.

Bars are ordered by close time. A gap becomes knowable only at the third bar's close.
The first later candle that overlaps the zone marks it mitigated; a later close through
the far edge marks it inverted (an IFVG). These are explicit detector conventions, not
claims that the patterns have predictive value.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite
from typing import Literal

Side = Literal["BULLISH", "BEARISH"]
GapState = Literal["ACTIVE", "MITIGATED", "INVERTED"]


@dataclass(frozen=True)
class Candle:
    candle_id: str
    closed_at: datetime
    high: float
    low: float
    close: float

    def validate(self) -> None:
        if not self.candle_id or self.closed_at.tzinfo is None:
            raise ValueError("candle requires an id and timezone-aware close time")
        if not all(isfinite(value) for value in (self.high, self.low, self.close)):
            raise ValueError("OHLC values must be finite")
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("candle prices are inconsistent")


@dataclass(frozen=True)
class FairValueGap:
    gap_id: str
    side: Side
    lower: float
    upper: float
    first_candle_id: str
    middle_candle_id: str
    confirming_candle_id: str
    available_at: datetime
    state: GapState = "ACTIVE"
    state_changed_at: datetime | None = None
    state_changed_by: str | None = None
    inverted_side: Side | None = None


def detect_fair_value_gaps(candles: list[Candle]) -> list[FairValueGap]:
    """Emit nonzero three-candle gaps; the middle candle's shape is not filtered."""
    for candle in candles:
        candle.validate()
    if any(left.closed_at >= right.closed_at for left, right in zip(candles, candles[1:])):
        raise ValueError("candles must be strictly ordered by close time")
    gaps: list[FairValueGap] = []
    for first, middle, third in zip(candles, candles[1:], candles[2:]):
        if first.high < third.low:
            side: Side = "BULLISH"
            lower, upper = first.high, third.low
        elif third.high < first.low:
            side = "BEARISH"
            lower, upper = third.high, first.low
        else:
            continue
        gaps.append(FairValueGap(
            gap_id=f"{first.candle_id}:{middle.candle_id}:{third.candle_id}:{side}",
            side=side, lower=lower, upper=upper, first_candle_id=first.candle_id,
            middle_candle_id=middle.candle_id, confirming_candle_id=third.candle_id,
            available_at=third.closed_at))
    return gaps


def advance_gap(gap: FairValueGap, candle: Candle) -> FairValueGap:
    """Apply one later candle; old snapshots remain immutable for point-in-time replay."""
    candle.validate()
    if candle.closed_at <= gap.available_at:
        raise ValueError("gap state can only advance on a candle after availability")
    if gap.state != "ACTIVE":
        return gap
    inverted = (gap.side == "BULLISH" and candle.close < gap.lower
                or gap.side == "BEARISH" and candle.close > gap.upper)
    if inverted:
        return replace(gap, state="INVERTED", state_changed_at=candle.closed_at,
                       state_changed_by=candle.candle_id,
                       inverted_side="BEARISH" if gap.side == "BULLISH" else "BULLISH")
    if candle.low <= gap.upper and candle.high >= gap.lower:
        return replace(gap, state="MITIGATED", state_changed_at=candle.closed_at,
                       state_changed_by=candle.candle_id)
    return gap
