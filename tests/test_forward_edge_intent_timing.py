"""FE-0056 intents wait for completed bars/quotes and decision latency."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.intent_timing import IntentTimingError, create_timed_intent, validate_fill_time
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

T0 = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _event(kind: EventKind, payload: dict) -> MarketEvent:
    return MarketEvent("event-1", kind, "NSE", "NSE:INFY", T0, T0, T0, payload, "fixture")


def test_completed_bar_or_quote_yields_latency_delayed_timed_intent() -> None:
    for event in (_event(EventKind.BAR, {"open": 100, "high": 101, "low": 99, "close": 100,
                                        "volume": 100, "complete": True}),
                  _event(EventKind.QUOTE, {"bid": 99, "ask": 101})):
        intent = create_timed_intent(decision_id="d1", event=event, side="BUY", quantity=1,
                                     decision_time=T0, latency=timedelta(milliseconds=250))
        assert intent.earliest_fill_time == T0 + timedelta(milliseconds=250)
        validate_fill_time(intent, intent.earliest_fill_time)
        with pytest.raises(IntentTimingError, match="precedes"):
            validate_fill_time(intent, T0)


def test_incomplete_bar_future_event_and_wrong_event_kind_are_rejected() -> None:
    with pytest.raises(IntentTimingError, match="complete"):
        create_timed_intent(decision_id="d", event=_event(EventKind.BAR, {"open": 1, "high": 1,
                              "low": 1, "close": 1, "volume": 1}), side="BUY", quantity=1,
                            decision_time=T0, latency=timedelta(0))
    later = MarketEvent("late", EventKind.QUOTE, "NSE", "NSE:INFY", T0, T0 + timedelta(seconds=1),
                        T0 + timedelta(seconds=1), {"bid": 99, "ask": 101}, "fixture")
    with pytest.raises(IntentTimingError, match="not available"):
        create_timed_intent(decision_id="d", event=later, side="BUY", quantity=1,
                            decision_time=T0, latency=timedelta(0))
    halt = _event(EventKind.HALT, {"status": "HALTED"})
    with pytest.raises(IntentTimingError, match="requires"):
        create_timed_intent(decision_id="d", event=halt, side="BUY", quantity=1,
                            decision_time=T0, latency=timedelta(0))
