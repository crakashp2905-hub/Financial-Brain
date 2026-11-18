"""One injectable UTC clock contract for market capture and paper-session decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


class ClockError(ValueError):
    """A source or simulated timestamp is naive, reversed, or non-monotonic."""


def as_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ClockError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class EventClocks:
    exchange_utc: datetime
    received_utc: datetime
    available_utc: datetime

    def __post_init__(self) -> None:
        exchange = as_utc(self.exchange_utc, "exchange clock")
        received = as_utc(self.received_utc, "receive clock")
        available = as_utc(self.available_utc, "availability clock")
        if not exchange <= received <= available:
            raise ClockError("event clocks must satisfy exchange <= receive <= available")


class SessionClockProvider:
    """Injectable wall/simulation clock that rejects backwards reads.

    Exchange timestamps are provider data and are passed to ``event_clocks``. Receive,
    availability, decision, ingest and simulated-execution clocks all come from this
    provider, so tests can inject skew and production can use one clock source.
    """

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._last: datetime | None = None

    def now_utc(self, purpose: str) -> datetime:
        value = as_utc(self._now_fn(), purpose)
        if self._last is not None and value < self._last:
            raise ClockError(f"{purpose} clock moved backwards")
        self._last = value
        return value

    def event_clocks(self, exchange_utc: datetime) -> EventClocks:
        exchange = as_utc(exchange_utc, "exchange clock")
        received = self.receive_time(exchange)
        available = self.availability_time(exchange, received)
        return EventClocks(exchange, received, available)

    def receive_time(self, exchange_utc: datetime) -> datetime:
        exchange = as_utc(exchange_utc, "exchange clock")
        received = self.now_utc("receive")
        if received < exchange:
            raise ClockError("event clocks must satisfy exchange <= receive <= available")
        return received

    def availability_time(self, exchange_utc: datetime, received_utc: datetime) -> datetime:
        exchange = as_utc(exchange_utc, "exchange clock")
        received = as_utc(received_utc, "receive clock")
        available = self.now_utc("availability")
        EventClocks(exchange, received, available)
        return available

    def decision_time(self, observed_utc: datetime | None = None) -> datetime:
        if observed_utc is None:
            return self.now_utc("decision")
        decision = as_utc(observed_utc, "decision clock")
        if self._last is not None and decision < self._last:
            raise ClockError("decision clock moved backwards")
        self._last = decision
        return decision

    def simulated_execution_time(self, decision_utc: datetime,
                                 observed_utc: datetime | str | None = None) -> datetime:
        decision = as_utc(decision_utc, "decision clock")
        if observed_utc is None:
            execution = self.now_utc("simulated execution")
        else:
            try:
                supplied = (datetime.fromisoformat(observed_utc) if isinstance(observed_utc, str)
                            else observed_utc)
                if not isinstance(supplied, datetime):
                    raise TypeError
                execution = as_utc(supplied, "simulated execution clock")
            except (TypeError, ValueError) as exc:
                raise ClockError("simulated execution clock is invalid") from exc
        if self._last is not None and execution < self._last:
            raise ClockError("simulated execution clock moved backwards")
        if execution < decision:
            raise ClockError("simulated execution clock precedes the decision")
        self._last = execution
        return execution

    @staticmethod
    def validate_decision(decision_utc: datetime, available_clocks: tuple[datetime, ...]) -> datetime:
        decision = as_utc(decision_utc, "decision clock")
        if any(as_utc(available, "availability clock") > decision for available in available_clocks):
            raise ClockError("an event available after the decision cannot inform it")
        return decision
