"""Intraday tools (FB-0370, FB-0371, FB-0373, FB-0376, FB-0379).

* ``opening_range`` takes the high and low of the first ``n`` bars of a session. Fewer bars than ``n`` gives None.
* ``anchored_vwap`` is volume-weighted average price from an anchor bar onward, using typical price.
* ``completed_htf_trend`` resamples bars into higher-timeframe bars and only uses bars that are complete at a given
  minute. An unfinished higher-timeframe bar never informs a decision.
* ``SessionControl`` pauses and resumes trading inside a session. A paused session refuses entries until resumed.
* ``ReplayControl`` pauses, steps and changes speed for replay. Stepping while running is refused.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


def opening_range(highs: Sequence[float], lows: Sequence[float], n: int) -> tuple[float, float] | None:
    if n < 1:
        raise ValueError("n must be positive")
    if len(highs) < n or len(lows) < n:
        return None
    return max(highs[:n]), min(lows[:n])


def anchored_vwap(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                  volumes: Sequence[float], anchor: int) -> float | None:
    if not (0 <= anchor < len(closes)):
        raise ValueError("anchor out of range")
    pv = 0.0
    vol = 0.0
    for i in range(anchor, len(closes)):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        pv += typical * volumes[i]
        vol += volumes[i]
    if vol == 0:
        return None
    return pv / vol


def completed_htf_trend(closes: Sequence[float], group: int, minute: int) -> int | None:
    """Trend (+1 up, -1 down) from the last completed group of ``group`` bars available at ``minute``.

    Only bars with index < minute are visible; the group containing minute is incomplete and is ignored.
    """
    if group < 2:
        raise ValueError("group must be at least 2")
    completed = minute // group
    if completed < 2:
        return None
    last_close = closes[completed * group - 1]
    prev_close = closes[(completed - 1) * group - 1]
    if last_close > prev_close:
        return 1
    if last_close < prev_close:
        return -1
    return 0


class SessionState(Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


@dataclass
class SessionControl:
    state: SessionState = SessionState.RUNNING

    def pause(self) -> None:
        self.state = SessionState.PAUSED

    def resume(self) -> None:
        self.state = SessionState.RUNNING

    def entry_allowed(self) -> bool:
        return self.state is SessionState.RUNNING


@dataclass
class ReplayControl:
    paused: bool = False
    speed: float = 1.0
    position: int = 0

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = speed

    def pause(self) -> None:
        self.paused = True

    def step(self) -> int:
        if not self.paused:
            raise RuntimeError("step is only allowed while paused")
        self.position += 1
        return self.position
