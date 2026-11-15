from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.market_data import (
    KiteQuoteNormalizationError,
    normalize_kite_quote,
)
from financial_brain.forward_edge.market_events import EventKind


def quote():
    return {"timestamp": "2026-11-13 10:15:00", "last_trade_time": "2026-11-13 10:14:59",
            "last_price": 101.5, "volume": 1000, "ohlc": {"open": 100.0, "high": 102.0, "low": 99.0},
            "depth": {"buy": [{"price": 101.4, "quantity": 12}],
                      "sell": [{"price": 101.6, "quantity": 9}]}}


def test_kite_quote_event_preserves_exchange_and_receipt_clocks_and_source():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    event = normalize_kite_quote("NSE:INFY", quote(), received_at=received)
    assert event.kind is EventKind.QUOTE
    assert event.venue == "NSE"
    assert event.instrument_id == "NSE:INFY"
    assert event.event_time.isoformat() == "2026-11-13T04:45:00+00:00"
    assert event.received_at == received
    assert event.available_at == received
    assert event.source == "KITE_CONNECT_READ_ONLY"
    assert event.payload["bid"] == 101.4 and event.payload["ask"] == 101.6
    assert event.payload["session_high"] == 102.0 and event.payload["session_low"] == 99.0


def test_missing_exchange_clock_is_refused():
    with pytest.raises(KiteQuoteNormalizationError, match="timestamp is missing"):
        normalize_kite_quote("NSE:INFY", quote() | {"timestamp": None},
                             received_at=datetime.now(timezone.utc))


def test_ltp_only_observation_is_archived_as_non_executable_tick():
    event = normalize_kite_quote("NSE:INFY", quote() | {"depth": None},
                                 received_at=datetime.now(timezone.utc))
    assert event.kind is EventKind.TICK
    assert event.payload["price"] == 101.5
    assert event.payload["non_executable_reason"] == "TWO_SIDED_DEPTH_UNAVAILABLE"
    assert "bid" not in event.payload and "ask" not in event.payload


def test_naive_receipt_clock_is_rejected_instead_of_claimed_as_observed_time():
    with pytest.raises(KiteQuoteNormalizationError, match="timezone aware"):
        normalize_kite_quote("NSE:INFY", quote(), received_at=datetime(2026, 11, 13, 10, 15))


def test_repeated_exchange_snapshot_at_a_later_receipt_keeps_both_observations():
    first = normalize_kite_quote("NSE:INFY", quote(),
                                 received_at=datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc))
    second = normalize_kite_quote("NSE:INFY", quote(),
                                  received_at=datetime(2026, 11, 13, 4, 45, 2, tzinfo=timezone.utc))
    assert first.event_time == second.event_time
    assert first.event_id != second.event_id
