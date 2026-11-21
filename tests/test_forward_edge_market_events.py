"""FE-0033 normalized market event contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent, MarketEventError

T0 = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _event(kind: EventKind, payload: dict, **kw) -> MarketEvent:
    base = dict(event_id="evt-1", kind=kind, venue="NSE", instrument_id="NSE:INFY",
                event_time=T0, received_at=T0 + timedelta(milliseconds=2),
                available_at=T0 + timedelta(milliseconds=3), payload=payload, source="kite-fixture",
                sequence=4, decision_time=T0 + timedelta(milliseconds=5), evidence_id="fixture:1")
    base.update(kw)
    return MarketEvent(**base)


@pytest.mark.parametrize("kind,payload", [
    (EventKind.TICK, {"price": 100.5, "quantity": 10}),
    (EventKind.QUOTE, {"bid": 100.0, "ask": 101.0, "bid_quantity": 5, "ask_quantity": 6}),
    (EventKind.BOOK_DEPTH, {"bids": [[100, 2], [99, 3]], "asks": [[101, 2], [102, 4]]}),
    (EventKind.BAR, {"open": 100, "high": 103, "low": 99, "close": 102, "volume": 10}),
    (EventKind.HALT, {"status": "HALTED"}),
    (EventKind.AUCTION, {"phase": "OPEN"}),
    (EventKind.CORPORATE_ACTION, {"action": "SPLIT", "ratio": [2, 1]}),
    (EventKind.BROKER_UPDATE, {"order_id": "p1", "state": "PARTIAL"}),
])
def test_all_market_event_kinds_roundtrip_with_clocks(kind: EventKind, payload: dict) -> None:
    event = _event(kind, payload)
    assert MarketEvent.from_dict(event.as_dict()) == event
    assert len(event.sha256()) == 64
    assert event.event_time.tzinfo == timezone.utc


def test_naive_clock_or_event_not_available_at_decision_is_rejected() -> None:
    with pytest.raises(MarketEventError, match="timezone aware"):
        _event(EventKind.TICK, {"price": 10}, received_at=datetime(2026, 11, 15))
    with pytest.raises(MarketEventError, match="not available"):
        _event(EventKind.TICK, {"price": 10}, decision_time=T0)


@pytest.mark.parametrize("kind,payload", [
    (EventKind.TICK, {"price": float("nan")}),
    (EventKind.QUOTE, {"bid": 102, "ask": 101}),
    (EventKind.BOOK_DEPTH, {"bids": [[99, 1], [100, 2]], "asks": []}),
    (EventKind.BAR, {"open": 100, "high": 99, "low": 98, "close": 99, "volume": -1}),
    (EventKind.HALT, {}),
])
def test_malformed_market_payloads_fail_closed(kind: EventKind, payload: dict) -> None:
    with pytest.raises(MarketEventError):
        _event(kind, payload)
