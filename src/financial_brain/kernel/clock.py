"""Time-valid instants (FB-0043, FB-0070 ordering).

Every market fact carries four instants. The ordering below is an invariant of the platform: a decision may only
use information that was available at or before it was taken, and an event cannot be ingested before it happened
or be available before it was ingested.

    event_time <= ingest_time <= available_time <= decision_time
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class ClockError(ValueError):
    """Naive timestamps, out-of-order instants and future-dated facts are rejected."""


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ClockError("expected a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ClockError("naive timestamps are not allowed; attach a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Instants:
    event_time: datetime
    ingest_time: datetime
    available_time: datetime
    decision_time: datetime

    def __post_init__(self) -> None:
        for name in ("event_time", "ingest_time", "available_time", "decision_time"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not (self.event_time <= self.ingest_time <= self.available_time <= self.decision_time):
            raise ClockError(
                "instants out of order: require event <= ingest <= available <= decision"
            )


def assert_not_future(instant: datetime, now: datetime) -> datetime:
    """A fact stamped after the reference clock cannot exist yet; refuse it."""
    t, n = utc(instant), utc(now)
    if t > n:
        raise ClockError(f"timestamp {t.isoformat()} is after the reference clock {n.isoformat()}")
    return t
