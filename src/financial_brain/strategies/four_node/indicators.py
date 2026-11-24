"""SMMA bands and prior-day Fibonacci levels (FB-2509 to FB-2511).

SMMA is Wilder smoothing (Pine ``ta.rma``): seeded with the simple mean of the first ``n`` values,
then ``s = (s * (n - 1) + x) / n``. Before ``n`` values exist the result is None (warm-up). A
missing bar (None) resets the series: no value is carried across a gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

FIB_RATIOS: tuple[float, ...] = (
    -323.4, -200.0, -161.8, -141.4, -100.0, -76.4, -61.8, -41.4, -27.0, -12.8, 0.0, 11.4, 23.6,
    38.2, 50.0, 61.8, 76.4, 88.6, 100.0, 112.8, 127.0, 141.4, 161.8, 176.4, 200.0, 261.8, 423.6)


SessionReset = Literal["CONTINUOUS", "ON_SESSION_CHANGE"]


def smma(values: list[float | None], n: int, *, session_ids: list[str] | None = None,
         reset_policy: SessionReset = "CONTINUOUS") -> list[float | None]:
    """Wilder SMMA, seeded by an n-observation SMA.

    Missing values always reset warm-up. Session changes reset only when requested; callers
    must provide one session id per value for that policy. This makes overnight handling an
    explicit, reproducible choice rather than inferring it from bar spacing.
    """
    if n < 1:
        raise ValueError("period must be positive")
    if reset_policy not in ("CONTINUOUS", "ON_SESSION_CHANGE"):
        raise ValueError("unknown session reset policy")
    if session_ids is not None and len(session_ids) != len(values):
        raise ValueError("session_ids must align with values")
    if reset_policy == "ON_SESSION_CHANGE" and session_ids is None:
        raise ValueError("session_ids are required for ON_SESSION_CHANGE")
    out: list[float | None] = []
    seed: list[float] = []
    s: float | None = None
    previous_session: str | None = None
    for index, v in enumerate(values):
        session = session_ids[index] if session_ids is not None else None
        if (reset_policy == "ON_SESSION_CHANGE" and previous_session is not None
                and session != previous_session):
            seed, s = [], None
        previous_session = session
        if v is None:
            seed, s = [], None
            out.append(None)
            continue
        if not isfinite(v):
            raise ValueError("SMMA inputs must be finite or None")
        if s is None:
            seed.append(v)
            if len(seed) == n:
                s = sum(seed) / n
            out.append(s)
        else:
            s = (s * (n - 1) + v) / n
            out.append(s)
    return out


@dataclass(frozen=True)
class Cluster20:
    low: float
    mid: float
    high: float


def cluster20(lows: list[float | None], closes: list[float | None],
              highs: list[float | None], *, session_ids: list[str] | None = None,
              reset_policy: SessionReset = "CONTINUOUS") -> list[Cluster20 | None]:
    if not (len(lows) == len(closes) == len(highs)):
        raise ValueError("OHLC arrays must have equal length")
    lo = smma(lows, 20, session_ids=session_ids, reset_policy=reset_policy)
    mi = smma(closes, 20, session_ids=session_ids, reset_policy=reset_policy)
    hi = smma(highs, 20, session_ids=session_ids, reset_policy=reset_policy)
    return [Cluster20(a, b, c) if None not in (a, b, c) else None  # type: ignore[arg-type]
            for a, b, c in zip(lo, mi, hi, strict=True)]


def close_smma_bands(closes: list[float | None], *, session_ids: list[str] | None = None,
                     reset_policy: SessionReset = "CONTINUOUS"
                     ) -> dict[int, list[float | None]]:
    """Aligned Close SMMAs required by the Four-Node study (8/50/100/200)."""
    return {period: smma(closes, period, session_ids=session_ids, reset_policy=reset_policy)
            for period in (8, 50, 100, 200)}


def fib_levels(prev_day_low: float, prev_day_high: float, anchor: str) -> dict[float, float]:
    """Levels from the *previous completed* day only. ``anchor`` is an owner-chosen variant."""
    if prev_day_high < prev_day_low:
        raise ValueError("high below low")
    rng = prev_day_high - prev_day_low
    if anchor == "PREV_DAY_LOW_TO_HIGH":
        return {r: prev_day_low + rng * r / 100 for r in FIB_RATIOS}
    if anchor == "PREV_DAY_HIGH_TO_LOW":
        return {r: prev_day_high - rng * r / 100 for r in FIB_RATIOS}
    raise ValueError(f"unknown Fibonacci anchor {anchor!r}")


@dataclass(frozen=True)
class DailyFibSnapshot:
    """Versioned levels tied to an explicitly completed source session."""

    version: str
    anchor: str
    anchor_session_id: str
    anchor_close_at: datetime
    evaluated_session_open_at: datetime
    levels: dict[float, float]


def daily_fib_snapshot(*, prev_day_low: float, prev_day_high: float, anchor: str,
                       anchor_session_id: str, anchor_close_at: datetime,
                       evaluated_session_open_at: datetime) -> DailyFibSnapshot:
    """Build levels only from an identified session closed before the evaluated session."""
    if not anchor_session_id.strip():
        raise ValueError("anchor_session_id is required")
    if anchor_close_at.tzinfo is None or evaluated_session_open_at.tzinfo is None:
        raise ValueError("session timestamps must be timezone-aware")
    if anchor_close_at >= evaluated_session_open_at:
        raise ValueError("anchor session must be completed before evaluated session opens")
    if not (isfinite(prev_day_low) and isfinite(prev_day_high)):
        raise ValueError("previous-session prices must be finite")
    return DailyFibSnapshot(
        version="four-node-daily-fib-v1", anchor=anchor,
        anchor_session_id=anchor_session_id, anchor_close_at=anchor_close_at,
        evaluated_session_open_at=evaluated_session_open_at,
        levels=fib_levels(prev_day_low, prev_day_high, anchor),
    )
