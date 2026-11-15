"""Injectable clock boundary for observed market and paper-session events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


class ClockError(ValueError):
    """Clock readings are missing, naive, or causally inconsistent."""


class ClockProvider(Protocol):
    """Supplies local UTC receipt, decision, and simulated-execution readings."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production provider backed by the host's UTC wall clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FixedClock:
    """Deterministic clock for replay and tests; advance by replacing its value."""

    value: datetime

    def __post_init__(self) -> None:
        _aware(self.value, "fixed clock")

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True)
class MarketClocks:
    """Source exchange timestamp plus local receipt and availability timestamps."""

    exchange_at: datetime
    received_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        _aware(self.exchange_at, "exchange timestamp")
        _aware(self.received_at, "receipt timestamp")
        _aware(self.available_at, "availability timestamp")
        if self.available_at < self.received_at:
            raise ClockError("availability cannot precede local receipt")


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ClockError(f"{label} must be timezone aware")
    return value


def capture_market_clocks(*, exchange_at: datetime, provider: ClockProvider,
                          availability_delay: timedelta = timedelta(0)) -> MarketClocks:
    """Capture local receipt once and derive availability without a second wall-clock read."""
    if availability_delay < timedelta(0):
        raise ClockError("availability delay cannot be negative")
    received = _aware(provider.now(), "receipt timestamp")
    return MarketClocks(exchange_at, received, received + availability_delay)


def capture_local_time(provider: ClockProvider, *, label: str) -> datetime:
    """Read and validate an injected decision or simulated-execution clock."""
    return _aware(provider.now(), label)


__all__ = ["ClockError", "ClockProvider", "FixedClock", "MarketClocks", "SystemClock",
           "capture_local_time", "capture_market_clocks"]
