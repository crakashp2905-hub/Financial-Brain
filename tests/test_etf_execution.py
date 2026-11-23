from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from financial_brain.etf_execution import resolve_etf_quote_fill
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

IST = ZoneInfo("Asia/Kolkata")


def _quote(at: datetime, *, depth: int = 10) -> MarketEvent:
    return MarketEvent(
        event_id="etf-q1", kind=EventKind.QUOTE, venue="NSE", instrument_id="NSE:ETFONE",
        event_time=at, received_at=at, available_at=at, source="KITE_CONNECT_READ_ONLY",
        payload={"bid": 99, "ask": 101, "bid_quantity": depth, "ask_quantity": depth,
                 "volume": 1000,
                 "depth": {"buy": [{"price": 99, "quantity": depth}],
                           "sell": [{"price": 101, "quantity": depth}]}},
    )


def test_no_quote_is_unpriced_and_does_not_synthesize_a_fill() -> None:
    now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    result = resolve_etf_quote_fill(quote=None, side="BUY", requested_units=2,
                                    execution_time=now)
    assert result.status == "UNPRICED" and result.filled_units == 0
    assert result.price is None and result.reason == "QUOTE_MISSING"


def test_fill_uses_quote_side_and_observed_depth_only() -> None:
    now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    quote = _quote(now)
    buy = resolve_etf_quote_fill(quote=quote, side="BUY", requested_units=2,
                                 execution_time=now)
    sell = resolve_etf_quote_fill(quote=quote, side="SELL", requested_units=2,
                                  execution_time=now)
    assert (buy.status, buy.price, buy.source_event_id) == ("FILLED", Decimal("101"), "etf-q1")
    assert sell.status == "FILLED" and sell.price == Decimal("99")
    partial = resolve_etf_quote_fill(quote=quote, side="BUY", requested_units=11,
                                     execution_time=now)
    assert partial.status == "PARTIAL" and partial.filled_units == 10
    assert partial.reason == "VISIBLE_DEPTH_LIMIT"


@pytest.mark.parametrize("delta", [timedelta(seconds=-6), timedelta(seconds=1)])
def test_stale_or_future_quote_is_unpriced(delta: timedelta) -> None:
    now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    quote = _quote(now + delta)
    result = resolve_etf_quote_fill(quote=quote, side="BUY", requested_units=1,
                                    execution_time=now)
    assert result.status == "UNPRICED" and result.price is None


def test_missing_or_invalid_quote_depth_never_uses_previous_close() -> None:
    now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    tick = MarketEvent(event_id="tick", kind=EventKind.TICK, venue="NSE",
                       instrument_id="NSE:ETFONE", event_time=now, received_at=now,
                       available_at=now, source="KITE_CONNECT_READ_ONLY",
                       payload={"price": 100, "previous_close": 100})
    result = resolve_etf_quote_fill(quote=tick, side="BUY", requested_units=1,
                                    execution_time=now)
    assert result.status == "UNPRICED" and result.reason == "QUOTE_NOT_EXECUTABLE"
