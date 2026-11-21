"""Signal-to-order timing contract: completed inputs first, earliest execution later (FE-0056)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from financial_brain.forward_edge.market_events import EventKind, MarketEvent


class IntentTimingError(ValueError):
    """Intent uses incomplete/unavailable information or an impossible fill clock."""


@dataclass(frozen=True)
class TimedIntent:
    decision_id: str
    instrument_id: str
    side: str
    quantity: int
    decision_time: datetime
    earliest_fill_time: datetime
    source_event_sha256: str


def create_timed_intent(*, decision_id: str, event: MarketEvent, side: str, quantity: int,
                        decision_time: datetime, latency: timedelta) -> TimedIntent:
    if not decision_id.strip() or side not in {"BUY", "SELL"} or quantity < 1:
        raise IntentTimingError("decision ID, valid side and positive quantity are required")
    if latency < timedelta(0):
        raise IntentTimingError("latency cannot be negative")
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise IntentTimingError("decision_time must be timezone aware")
    decision = decision_time.astimezone(timezone.utc)
    if event.kind == EventKind.BAR and event.payload.get("complete") is not True:
        raise IntentTimingError("bar must be explicitly complete before intent generation")
    if event.kind not in {EventKind.BAR, EventKind.QUOTE}:
        raise IntentTimingError("intent requires a completed bar or quote event")
    if event.available_at > decision:
        raise IntentTimingError("signal event was not available at decision time")
    earliest = decision + latency
    if earliest < event.available_at:
        raise IntentTimingError("earliest fill precedes event availability")
    return TimedIntent(decision_id, event.instrument_id, side, quantity, decision, earliest, event.sha256())


def validate_fill_time(intent: TimedIntent, fill_time: datetime) -> None:
    if fill_time.tzinfo is None or fill_time.utcoffset() is None:
        raise IntentTimingError("fill_time must be timezone aware")
    if fill_time.astimezone(timezone.utc) < intent.earliest_fill_time:
        raise IntentTimingError("fill precedes decision plus latency")
