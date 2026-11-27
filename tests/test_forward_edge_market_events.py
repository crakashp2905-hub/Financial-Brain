"""FE-0033 normalized market event contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace

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
    (EventKind.NAV, {"nav_per_unit": 125.4, "nav_date": "2026-11-14"}),
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


def test_bar_completion_marker_must_be_an_explicit_boolean() -> None:
    payload = {"open": 100, "high": 103, "low": 99, "close": 102, "volume": 10}
    for invalid in (1, 0, "true", None):
        with pytest.raises(MarketEventError, match="complete marker must be boolean"):
            _event(EventKind.BAR, payload | {"complete": invalid})


def test_explicit_source_exchange_and_publication_timestamps_roundtrip() -> None:
    published = T0 - timedelta(seconds=1)
    event = _event(EventKind.TICK, {"price": 10}, source_at=T0 - timedelta(seconds=2),
                   exchange_at=T0 - timedelta(milliseconds=1), published_at=published)
    restored = MarketEvent.from_dict(event.as_dict())
    assert restored.source_at == event.source_at
    assert restored.exchange_at == event.exchange_at
    assert restored.published_at == published
    assert event.as_dict()["received_at"] == event.received_at.isoformat()
    assert event.as_dict()["available_at"] == event.available_at.isoformat()
    assert "computed_at" not in event.as_dict()  # legacy event hashes stay stable when absent


def test_computation_clock_roundtrips_and_is_available_before_decision() -> None:
    computed = T0 + timedelta(milliseconds=4)
    event = _event(EventKind.TICK, {"price": 10}, computed_at=computed)
    restored = MarketEvent.from_dict(event.as_dict())
    assert restored.computed_at == computed
    assert restored.decision_time >= computed

    with pytest.raises(MarketEventError, match="computed event cannot precede"):
        _event(EventKind.TICK, {"price": 10}, computed_at=T0)
    with pytest.raises(MarketEventError, match="not available"):
        _event(EventKind.TICK, {"price": 10}, computed_at=T0 + timedelta(milliseconds=6))


def test_publication_cannot_follow_strategy_availability() -> None:
    with pytest.raises(MarketEventError, match="before publication"):
        _event(EventKind.TICK, {"price": 10}, published_at=T0 + timedelta(seconds=4))


def _cross_asset_clock_event(family: str) -> MarketEvent:
    start = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)
    intervals = {"BAR_1M": 60, "BAR_5M": 300, "BAR_15M": 900}
    if family in intervals:
        interval = intervals[family]
        observed = start + timedelta(seconds=interval)
        kind, instrument, venue = EventKind.BAR, "NSE:INFY", "NSE"
        payload = {"open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 100,
                   "complete": True, "interval_seconds": interval,
                   "bar_start_at": start.isoformat(), "bar_end_at": observed.isoformat()}
    elif family in {"FUTURES", "OPTIONS"}:
        observed = start + timedelta(seconds=1)
        kind, venue = EventKind.QUOTE, "NFO"
        instrument = "NFO:NIFTY26DEC25000FUT" if family == "FUTURES" else "NFO:NIFTY26DEC25000CE"
        payload = {"bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 12}
    else:
        observed = start + timedelta(days=1)
        kind, instrument, venue = EventKind.NAV, "MF:TEST-REGULAR-GROWTH", "AMFI"
        payload = {"nav_per_unit": 100.0, "nav_date": "2026-11-14"}
    published = observed + timedelta(milliseconds=100)
    available = published + timedelta(milliseconds=200)
    decision = available + timedelta(milliseconds=300)
    return MarketEvent(family.lower(), kind, venue, instrument, observed, available, available,
                       payload, "CLOCK_FIXTURE", decision_time=decision, published_at=published)


@pytest.mark.parametrize("family", ["BAR_1M", "BAR_5M", "BAR_15M", "FUTURES", "OPTIONS", "MUTUAL_FUND"])
def test_cross_asset_publication_and_availability_clock_mutations_fail_closed(family: str) -> None:
    event = _cross_asset_clock_event(family)
    assert event.published_at <= event.available_at <= event.decision_time
    with pytest.raises(MarketEventError, match="before publication"):
        replace(event, published_at=event.available_at + timedelta(microseconds=1))
    with pytest.raises(MarketEventError, match="not available"):
        replace(event, available_at=event.decision_time + timedelta(microseconds=1))


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
