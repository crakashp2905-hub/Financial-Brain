"""Read-only Kite quote adapter contracts; no network or credentials required."""
from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.market_data import DataError, decision_visible
from financial_brain.ingest.kite_live import forward_events


RECEIPT = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def test_full_quote_becomes_timestamped_forward_event():
    calls = []

    def quote(keys, kind):
        calls.append((keys, kind))
        return {"NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "last_price": 1500.5}}

    event, = forward_events(["INFY"], quote_fn=quote, received_at=RECEIPT)
    assert calls == [(["NSE:INFY"], "full")]
    assert event.instrument == "NSE:INFY"
    assert event.event_utc == datetime(2026, 11, 12, 3, 59, 59, tzinfo=timezone.utc)
    assert event.received_utc == event.available_utc == RECEIPT
    assert dict(event.payload) == {"exchange": "NSE", "last_price": "1500.5", "source": "kite_connect"}
    assert not decision_visible(event, datetime(2026, 11, 12, 3, 59, 59, tzinfo=timezone.utc))


@pytest.mark.parametrize("reply", [
    {},
    {"NSE:INFY": {"last_price": 1500}},
    {"NSE:INFY": {"timestamp": "not-a-time"}},
    {"NSE:INFY": {"timestamp": "2026-11-12 09:30:01"}},
])
def test_missing_or_invalid_exchange_evidence_fails_closed(reply):
    with pytest.raises(DataError):
        forward_events(["INFY"], quote_fn=lambda *_: reply, received_at=RECEIPT)


def test_duplicate_symbols_and_naive_receipt_rejected():
    with pytest.raises(DataError, match="unique"):
        forward_events(["INFY", "INFY"], quote_fn=lambda *_: {})
    with pytest.raises(DataError, match="timezone-aware"):
        forward_events(["INFY"], quote_fn=lambda *_: {}, received_at=datetime(2026, 11, 12))
