from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.market_data import (
    KiteQuoteNormalizationError,
    normalize_kite_quote,
)
from financial_brain.forward_edge.market_events import EventKind


def quote():
    return {"timestamp": "2026-11-13 10:15:00", "last_trade_time": "2026-11-13 10:14:59",
            "last_price": 101.5, "volume": 1000,
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


@pytest.mark.parametrize("patch, message", [
    ({"timestamp": None}, "timestamp is missing"),
    ({"depth": {"buy": [], "sell": [{"price": 2.0}]}}, "both bid and ask"),
])
def test_missing_exchange_clock_or_two_sided_depth_is_refused(patch, message):
    row = quote() | patch
    with pytest.raises(KiteQuoteNormalizationError, match=message):
        normalize_kite_quote("NSE:INFY", row, received_at=datetime.now(timezone.utc))


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
