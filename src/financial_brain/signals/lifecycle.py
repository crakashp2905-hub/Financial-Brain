"""Append-only signal lifecycle state machine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LifecycleError(ValueError):
    pass


class SignalState(StrEnum):
    DETECTED = "DETECTED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    SHADOW = "SHADOW"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    MISSED = "MISSED"
    EXITED = "EXITED"
    EXPIRED = "EXPIRED"


TRANSITIONS = {
    SignalState.DETECTED: {SignalState.ELIGIBLE, SignalState.REJECTED, SignalState.EXPIRED},
    SignalState.ELIGIBLE: {SignalState.APPROVED, SignalState.SHADOW, SignalState.REJECTED,
                           SignalState.EXPIRED},
    SignalState.APPROVED: {SignalState.SUBMITTED, SignalState.SHADOW, SignalState.REJECTED,
                           SignalState.EXPIRED},
    SignalState.SHADOW: {SignalState.SUBMITTED, SignalState.PARTIAL, SignalState.FILLED,
                         SignalState.MISSED, SignalState.EXPIRED},
    SignalState.SUBMITTED: {SignalState.PARTIAL, SignalState.FILLED, SignalState.MISSED,
                            SignalState.EXPIRED},
    SignalState.PARTIAL: {SignalState.PARTIAL, SignalState.FILLED, SignalState.MISSED,
                          SignalState.EXPIRED},
    SignalState.FILLED: {SignalState.EXITED},
}


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    event_id: str
    signal_id: str
    state: SignalState
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SignalLifecycle:
    signal_id: str
    state: SignalState = SignalState.DETECTED
    events: tuple[LifecycleEvent, ...] = ()

    def advance(self, event: LifecycleEvent) -> "SignalLifecycle":
        if event.signal_id != self.signal_id or not event.event_id:
            raise LifecycleError("event must have matching signal_id and event_id")
        prior = next((item for item in self.events if item.event_id == event.event_id), None)
        if prior is not None:
            if prior != event:
                raise LifecycleError("event_id reused with different lifecycle content")
            return self
        if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() is None:
            raise LifecycleError("lifecycle timestamp must be timezone-aware")
        if self.events and event.occurred_at < self.events[-1].occurred_at:
            raise LifecycleError("lifecycle events must be event-time ordered")
        if event.state not in TRANSITIONS.get(self.state, set()):
            raise LifecycleError(f"invalid signal lifecycle transition: {self.state} -> {event.state}")
        return SignalLifecycle(self.signal_id, event.state, self.events + (event,))
