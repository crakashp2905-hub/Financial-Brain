"""Session-bounded streaming OHLCV aggregation with explicit gaps and close-time semantics.

The caller supplies a sourced session interval and completed one-minute bars. Duplicate identical
minutes are idempotent; conflicting revisions are rejected rather than silently changing output.
An interval is readable only after its end and only from minute observations available by the
requested as-of clock. Missing minutes are reported; prices are never forward-filled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal


BarFrequency = Literal["1m", "5m", "15m", "30m", "1h", "1D"]
_FREQUENCIES: dict[str, int | None] = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": None}


class StreamingBarError(ValueError):
    """Invalid minute input, frequency, session clock or conflicting revision."""


class BarWindowStatus(StrEnum):
    COMPLETE = "COMPLETE"
    MISSING_MINUTES = "MISSING_MINUTES"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StreamingBarError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MinuteBar:
    instrument_id: str
    start_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    available_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            raise StreamingBarError("instrument_id is required")
        start, available = _utc(self.start_at, "minute start"), _utc(self.available_at, "minute availability")
        if available < start + timedelta(minutes=1):
            raise StreamingBarError("minute bar cannot be available before its interval has completed")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
               for value in values):
            raise StreamingBarError("OHLCV values must be finite numbers")
        if (min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0
                or self.high < max(self.open, self.close, self.low)
                or self.low > min(self.open, self.close, self.high)):
            raise StreamingBarError("minute OHLCV values are inconsistent")
        if start.second or start.microsecond:
            raise StreamingBarError("minute start must be aligned to a whole minute")
        object.__setattr__(self, "start_at", start)
        object.__setattr__(self, "available_at", available)


@dataclass(frozen=True)
class BarWindow:
    instrument_id: str
    frequency: BarFrequency
    start_at: datetime
    end_at: datetime
    as_of: datetime
    status: BarWindowStatus
    expected_minutes: int
    observed_minutes: int
    missing_minute_starts: tuple[datetime, ...]
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None

    @property
    def is_complete(self) -> bool:
        return self.status is BarWindowStatus.COMPLETE


class StreamingBarAggregator:
    """Deterministic in-memory aggregator for one instrument and one sourced exchange session."""

    def __init__(self, *, instrument_id: str, session_open: datetime, session_close: datetime) -> None:
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise StreamingBarError("instrument_id is required")
        opened, closed = _utc(session_open, "session_open"), _utc(session_close, "session_close")
        duration = (closed - opened).total_seconds()
        if duration <= 0 or duration % 60:
            raise StreamingBarError("session must have a positive whole-minute duration")
        if opened.second or opened.microsecond or closed.second or closed.microsecond:
            raise StreamingBarError("session boundaries must align to whole minutes")
        if opened.date() != (closed - timedelta(microseconds=1)).date():
            raise StreamingBarError("session must not cross a UTC date boundary")
        self.instrument_id = instrument_id
        self.session_open = opened
        self.session_close = closed
        self._bars: dict[datetime, MinuteBar] = {}
        self._session_minutes = int(duration // 60)

    def ingest(self, bar: MinuteBar) -> bool:
        """Store a minute; return False for an exact duplicate, True for a new observation."""
        if bar.instrument_id != self.instrument_id:
            raise StreamingBarError("minute instrument differs from the frozen aggregator instrument")
        if bar.start_at < self.session_open or bar.start_at + timedelta(minutes=1) > self.session_close:
            raise StreamingBarError("minute lies outside the sourced session interval")
        prior = self._bars.get(bar.start_at)
        if prior is not None:
            if prior != bar:
                raise StreamingBarError("conflicting minute revision; append a corrected session version")
            return False
        self._bars[bar.start_at] = bar
        return True

    def snapshot(self, *, as_of: datetime, frequencies: tuple[BarFrequency, ...] =
                 ("1m", "5m", "15m", "30m", "1h", "1D")) -> tuple[BarWindow, ...]:
        """Return only ended windows. Incomplete windows carry missing-minute flags and null OHLCV."""
        cutoff = _utc(as_of, "as_of")
        if cutoff < self.session_open:
            return ()
        if len(frequencies) != len(set(frequencies)):
            raise StreamingBarError("frequencies must be unique")
        unknown = set(frequencies) - set(_FREQUENCIES)
        if unknown:
            raise StreamingBarError(f"unsupported frequencies: {sorted(unknown)}")
        visible = {start: row for start, row in self._bars.items() if row.available_at <= cutoff}
        out: list[BarWindow] = []
        for frequency in frequencies:
            nominal_minutes = _FREQUENCIES[frequency]
            width = self._session_minutes if nominal_minutes is None else nominal_minutes
            # Fixed intraday windows are emitted only when their full duration fits in
            # the sourced session. The daily bar covers the complete exchange session.
            for offset in range(0, self._session_minutes - width + 1, width):
                start = self.session_open + timedelta(minutes=offset)
                end = start + timedelta(minutes=width)
                if end > cutoff:
                    continue
                expected = tuple(start + timedelta(minutes=minute) for minute in range(width))
                present = [visible[minute] for minute in expected if minute in visible]
                missing = tuple(minute for minute in expected if minute not in visible)
                complete = not missing
                out.append(BarWindow(
                    instrument_id=self.instrument_id, frequency=frequency, start_at=start, end_at=end,
                    as_of=cutoff, status=BarWindowStatus.COMPLETE if complete else BarWindowStatus.MISSING_MINUTES,
                    expected_minutes=width, observed_minutes=len(present), missing_minute_starts=missing,
                    open=present[0].open if complete else None,
                    high=max(row.high for row in present) if complete else None,
                    low=min(row.low for row in present) if complete else None,
                    close=present[-1].close if complete else None,
                    volume=sum(row.volume for row in present) if complete else None,
                ))
        return tuple(sorted(out, key=lambda row: (row.end_at, row.frequency)))


__all__ = ["BarFrequency", "BarWindow", "BarWindowStatus", "MinuteBar", "StreamingBarAggregator",
           "StreamingBarError"]
