"""Causal availability gates for decisions that consume multiple observations."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone


class AvailabilityError(ValueError):
    """Decision inputs are missing, lack an availability clock or arrive too late."""


def _aware_utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AvailabilityError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def latest_input_available_at(inputs: Iterable[object]) -> datetime:
    """Return max(input.available_at), the first instant all decision facts can be used.

    Exchange/event timestamps are intentionally not substituted for availability timestamps.
    Each input may be a datetime availability value or an object with `available_at`.
    """
    values = list(inputs)
    if not values:
        raise AvailabilityError("decision must have at least one input availability timestamp")
    instants = []
    for value in values:
        available = value if isinstance(value, datetime) else getattr(value, "available_at", None)
        instants.append(_aware_utc(available, "input available_at"))
    return max(instants)


def require_inputs_available(inputs: Iterable[object], decision_at: datetime) -> datetime:
    """Fail when any input became available after the decision; return the required cutoff."""
    latest = latest_input_available_at(inputs)
    decision = _aware_utc(decision_at, "decision_at")
    if latest > decision:
        raise AvailabilityError("decision time precedes the latest input availability")
    return latest


__all__ = ["AvailabilityError", "latest_input_available_at", "require_inputs_available"]
