from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.signals.lifecycle import (LifecycleError, LifecycleEvent,
                                               SignalLifecycle, SignalState)

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def event(key, state, offset=0):
    return LifecycleEvent(key, "s1", state, NOW + timedelta(seconds=offset))


def test_happy_signal_lifecycle_and_duplicate_event_are_idempotent():
    lifecycle = SignalLifecycle("s1")
    for value in [event("1", SignalState.ELIGIBLE), event("2", SignalState.APPROVED, 1),
                  event("3", SignalState.SUBMITTED, 2), event("4", SignalState.PARTIAL, 3),
                  event("5", SignalState.FILLED, 4), event("6", SignalState.EXITED, 5)]:
        lifecycle = lifecycle.advance(value)
    assert lifecycle.state == SignalState.EXITED
    assert lifecycle.advance(event("6", SignalState.EXITED, 5)) == lifecycle


def test_reject_invalid_transition_event_time_and_reused_id():
    lifecycle = SignalLifecycle("s1")
    with pytest.raises(LifecycleError, match="invalid"):
        lifecycle.advance(event("1", SignalState.FILLED))
    eligible = lifecycle.advance(event("1", SignalState.ELIGIBLE))
    with pytest.raises(LifecycleError, match="reused"):
        eligible.advance(event("1", SignalState.REJECTED))
    with pytest.raises(LifecycleError, match="ordered"):
        eligible.advance(event("2", SignalState.APPROVED, -1))
