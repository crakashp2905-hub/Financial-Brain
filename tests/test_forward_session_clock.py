from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.session_clock import ClockError, SessionClockProvider


T0 = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _sequence(*values):
    values = iter(values)
    return SessionClockProvider(lambda: next(values))


def test_event_clock_provider_captures_exchange_receive_and_availability_in_order():
    clock = _sequence(T0 + timedelta(milliseconds=1), T0 + timedelta(milliseconds=2))
    event = clock.event_clocks(T0)
    assert event.exchange_utc == T0
    assert event.received_utc == T0 + timedelta(milliseconds=1)
    assert event.available_utc == T0 + timedelta(milliseconds=2)


def test_event_clock_provider_rejects_exchange_skew_and_backwards_local_clock():
    with pytest.raises(ClockError, match="exchange <= receive"):
        _sequence(T0, T0 + timedelta(milliseconds=1)).event_clocks(T0 + timedelta(seconds=1))

    clock = _sequence(T0 + timedelta(seconds=2), T0 + timedelta(seconds=1))
    with pytest.raises(ClockError, match="moved backwards"):
        clock.event_clocks(T0)


def test_clock_provider_rejects_simulated_execution_before_decision():
    clock = _sequence(T0 - timedelta(seconds=1))
    with pytest.raises(ClockError, match="precedes the decision"):
        clock.simulated_execution_time(T0)


def test_future_event_is_never_visible_to_an_earlier_decision():
    from financial_brain.forward_edge.market_data import NormalizedEvent, decision_visible

    future = NormalizedEvent("QUOTE", "NSE:INFY", T0, T0 + timedelta(seconds=1),
                             T0 + timedelta(seconds=2))
    assert not decision_visible(future, T0 + timedelta(seconds=1))
