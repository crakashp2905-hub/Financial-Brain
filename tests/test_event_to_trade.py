from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.evaluation.event_to_trade import (
    EventOrder, EventQuote, ExchangeSession, simulate_event_order,
)


IST = timezone(timedelta(hours=5, minutes=30))
OPEN = datetime(2026, 11, 6, 9, 15, tzinfo=IST)
CLOSE = OPEN + timedelta(hours=6, minutes=15)
NEXT_OPEN = OPEN + timedelta(days=1)
NEXT_CLOSE = NEXT_OPEN + timedelta(hours=6, minutes=15)


def order(*, published=None, detected=None, arrival=None, qty=10):
    return EventOrder(published or OPEN, detected or OPEN + timedelta(minutes=1),
                      arrival or OPEN + timedelta(minutes=2), "BUY", qty,
                      timedelta(hours=30))


def quote(at, *, available=None, bid=99, ask=100, bid_qty=10, ask_qty=10):
    return EventQuote(at, available or at + timedelta(milliseconds=100),
                      bid, ask, bid_qty, ask_qty)


def test_intraday_event_respects_publication_detection_arrival_and_depth():
    event_order = order()
    quotes = (quote(OPEN + timedelta(minutes=1)),
              quote(OPEN + timedelta(minutes=3), ask=101, ask_qty=4))
    result = simulate_event_order(event_order, (ExchangeSession(OPEN, CLOSE),), quotes,
                                  estimated_cost_per_unit=0.5)
    assert result.status == "PARTIAL"
    assert result.filled_quantity == 4 and result.unfilled_quantity == 6
    assert result.average_price == 101
    assert result.estimated_cost == 2
    assert result.detection_delay_ms == 60_000
    assert result.broker_delay_ms == 60_000
    assert not result.after_hours_arrival


def test_after_hours_event_waits_for_next_open_quote():
    event_order = order(published=CLOSE + timedelta(minutes=30),
                        detected=CLOSE + timedelta(minutes=31),
                        arrival=CLOSE + timedelta(minutes=32))
    quote_before_arrival = quote(CLOSE - timedelta(seconds=1))
    next_day_quote = quote(NEXT_OPEN + timedelta(minutes=1), ask=105)
    result = simulate_event_order(
        event_order, (ExchangeSession(OPEN, CLOSE), ExchangeSession(NEXT_OPEN, NEXT_CLOSE)),
        (quote_before_arrival, next_day_quote), estimated_cost_per_unit=1)
    assert result.status == "FILLED" and result.average_price == 105
    assert result.after_hours_arrival
    assert result.fill_times == (next_day_quote.available_at,)


def test_stale_or_after_close_available_quote_is_not_backfilled():
    event_order = order(arrival=CLOSE - timedelta(minutes=2),
                        detected=CLOSE - timedelta(minutes=3),
                        published=CLOSE - timedelta(minutes=4))
    delayed = quote(CLOSE - timedelta(minutes=1), available=CLOSE + timedelta(seconds=1))
    stale = quote(CLOSE - timedelta(minutes=1), available=CLOSE - timedelta(seconds=1))
    result = simulate_event_order(event_order, (ExchangeSession(OPEN, CLOSE),),
                                  (stale, delayed), estimated_cost_per_unit=0)
    assert result.status == "NO_FILL" and result.filled_quantity == 0
    assert result.average_price is None and result.after_hours_arrival is False


def test_rejects_out_of_order_event_times_and_naive_session():
    with pytest.raises(ValueError, match="out of order"):
        simulate_event_order(order(detected=OPEN - timedelta(seconds=1)),
                             (ExchangeSession(OPEN, CLOSE),), (), estimated_cost_per_unit=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        simulate_event_order(order(), (ExchangeSession(datetime(2026, 11, 6, 9, 15), CLOSE),),
                             (), estimated_cost_per_unit=0)
