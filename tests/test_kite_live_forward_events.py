from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_data import KiteQuoteNormalizationError, executable_quote
from financial_brain.forward_edge.market_events import EventKind
from financial_brain.ingest.kite_live import forward_events


RECEIVED = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)


def full_quote(**overrides):
    quote = {
        "timestamp": "2026-11-13 10:15:00",
        "last_price": 101.5,
        "volume": 1000,
        "mode": "OPEN",
        "ohlc": {"open": 100.0, "high": 102.0, "low": 99.0, "close": 98.5},
        "depth": {
            "buy": [{"price": 101.4, "quantity": 12}, {"price": 101.3, "quantity": 20}],
            "sell": [{"price": 101.6, "quantity": 9}, {"price": 101.7, "quantity": 14}],
        },
    }
    quote.update(overrides)
    return quote


def test_forward_events_preserves_executable_depth_ohlc_status_and_source_hash():
    event, = forward_events(["INFY"], quote_fn=lambda keys, mode: {keys[0]: full_quote()},
                            received_at=RECEIVED)

    executable = executable_quote(event)
    assert event.kind is EventKind.QUOTE
    assert executable is not None
    assert executable["bid"] == Decimal("101.4") and executable["ask"] == Decimal("101.6")
    assert executable["bid_quantity"] == 12 and executable["ask_quantity"] == 9
    assert len(executable["depth"]["buy"]) == 2 and len(executable["depth"]["sell"]) == 2
    assert event.payload["volume"] == 1000
    assert event.payload["session_high"] == 102.0 and event.payload["session_low"] == 99.0
    assert event.payload["exchange_status"] == "OPEN"
    assert event.evidence_id and len(event.evidence_id) == 64
    assert event.source == "KITE_CONNECT_READ_ONLY"


def test_forward_events_keeps_ltp_only_data_non_executable():
    event, = forward_events(["INFY"], quote_fn=lambda keys, mode: {
        keys[0]: full_quote(depth=None)}, received_at=RECEIVED)

    assert event.kind is EventKind.TICK
    assert executable_quote(event) is None


def test_forward_events_rejects_missing_exchange_time_without_substitution():
    with pytest.raises(KiteQuoteNormalizationError, match="timestamp is missing"):
        forward_events(["INFY"], quote_fn=lambda keys, mode: {
            keys[0]: full_quote(timestamp=None)}, received_at=RECEIVED)


def test_forward_events_can_return_hashed_rejection_rows_for_bad_symbols():
    rejected = []
    events = forward_events(["INFY", "TCS"], quote_fn=lambda keys, mode: {
        keys[0]: full_quote(timestamp=None)}, received_at=RECEIVED, rejections_out=rejected)

    assert events == []
    assert rejected[0]["instrument"] == "NSE:INFY"
    assert rejected[0]["reason"] == "Kite exchange timestamp is missing"
    assert len(rejected[0]["event_sha256"]) == 64
    assert rejected[1]["instrument"] == "NSE:TCS"
    assert rejected[1]["reason"] == "MISSING_KITE_QUOTE"
    assert len(rejected[1]["event_sha256"]) == 64
