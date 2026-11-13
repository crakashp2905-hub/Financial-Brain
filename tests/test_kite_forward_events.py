"""Read-only Kite quote adapter contracts; no network or credentials required."""
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_data import DataError, decision_visible, executable_quote
from financial_brain.ingest.kite_live import forward_events


RECEIPT = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
INGESTED = datetime(2026, 11, 12, 4, 0, 0, 125000, tzinfo=timezone.utc)


def test_full_quote_becomes_timestamped_forward_event():
    calls = []

    def quote(keys, kind):
        calls.append((keys, kind))
        return {"NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "last_price": 1500.5,
                              "volume": 1000, "mode": "continuous", "ohlc": {"high": 1510, "low": 1490},
                              "depth": {"buy": [{"price": 1500.4, "quantity": 50}],
                                        "sell": [{"price": 1500.6, "quantity": 40}]}}}

    event, = forward_events(["INFY"], quote_fn=quote, received_at=RECEIPT, ingested_at=INGESTED)
    assert calls == [(["NSE:INFY"], "full")]
    assert event.instrument == "NSE:INFY"
    assert event.event_utc == datetime(2026, 11, 12, 3, 59, 59, tzinfo=timezone.utc)
    assert event.received_utc == event.available_utc == RECEIPT
    assert event.ingested_utc == INGESTED
    payload = dict(event.payload)
    assert payload["exchange"] == "NSE"
    assert payload["source"] == "kite_connect"
    assert payload["last_price"] == "1500.5"
    assert payload["quote_fidelity"] == "EXECUTABLE"
    assert payload["bid_price"] == "1500.4" and payload["ask_price"] == "1500.6"
    assert payload["bid_qty"] == "50" and payload["ask_qty"] == "40"
    assert payload["volume"] == "1000" and payload["session_high"] == "1510"
    assert payload["exchange_timestamp_age_ms"] == "1000"
    assert len(payload["raw_sha256"]) == 64
    assert executable_quote(event) == (True, "EXECUTABLE_TOP_OF_BOOK")
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


def test_source_tolerance_rejects_future_and_stale_exchange_timestamps_without_repair():
    replies = {
        "NSE:INFY": {"timestamp": "2026-11-12 09:00:00", "last_price": 1500},
    }
    rejected = []
    events = forward_events(["INFY"], quote_fn=lambda *_: replies, received_at=RECEIPT,
                            rejections_out=rejected, source_timestamp_tolerance=timedelta(seconds=5))
    assert events == []
    assert rejected[0]["reason"] == "STALE_EXCHANGE_TIMESTAMP"

    replies["NSE:INFY"]["timestamp"] = "2026-11-12 10:00:00"
    rejected.clear()
    events = forward_events(["INFY"], quote_fn=lambda *_: replies, received_at=RECEIPT,
                            rejections_out=rejected, source_timestamp_tolerance=timedelta(seconds=5))
    assert events == []
    assert rejected[0]["reason"] == "FUTURE_EXCHANGE_TIMESTAMP"

    with pytest.raises(DataError, match="non-negative timedelta"):
        forward_events(["INFY"], quote_fn=lambda *_: {}, received_at=RECEIPT,
                       source_timestamp_tolerance=timedelta(seconds=-1))


def test_duplicate_symbols_and_naive_receipt_rejected():
    with pytest.raises(DataError, match="unique"):
        forward_events(["INFY", "INFY"], quote_fn=lambda *_: {})
    with pytest.raises(DataError, match="timezone-aware"):
        forward_events(["INFY"], quote_fn=lambda *_: {}, received_at=datetime(2026, 11, 12))
    calls = []
    with pytest.raises(DataError, match="ingest clock"):
        forward_events(["INFY"], quote_fn=lambda *args: calls.append(args) or {}, received_at=RECEIPT,
                       ingested_at=RECEIPT - timedelta(microseconds=1))
    assert calls == []


def test_ltp_only_is_analyzable_but_not_fill_capable():
    event, = forward_events(["INFY"], quote_fn=lambda *_: {
        "NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "last_price": 1500.5}
    }, received_at=RECEIPT)
    assert dict(event.payload)["quote_fidelity"] == "LTP_ONLY"
    assert executable_quote(event) == (False, "LTP_ONLY_OR_MISSING_DEPTH")


def test_crossed_or_empty_depth_is_not_executable():
    event, = forward_events(["INFY"], quote_fn=lambda *_: {
        "NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "depth": {
            "buy": [{"price": 101, "quantity": 5}], "sell": [{"price": 100, "quantity": 5}]}}
    }, received_at=RECEIPT)
    assert executable_quote(event) == (False, "INVALID_OR_EMPTY_BOOK")


def test_opt_in_rejection_capture_keeps_valid_quotes_and_hashes_bad_rows():
    rejected = []
    events = forward_events(
        ["INFY", "TCS"],
        quote_fn=lambda *_: {
            "NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "last_price": 1500},
            "NSE:TCS": {"timestamp": "invalid", "last_price": 3500},
        },
        received_at=RECEIPT,
        rejections_out=rejected,
    )
    assert [event.instrument for event in events] == ["NSE:INFY"]
    assert len(rejected) == 1
    assert rejected[0]["instrument"] == "NSE:TCS"
    assert rejected[0]["reason"] == "INVALID_EXCHANGE_TIMESTAMP"
    assert len(rejected[0]["event_sha256"]) == 64


def test_injected_clock_provider_stamps_each_kite_event_after_exchange_time():
    from financial_brain.forward_edge.session_clock import SessionClockProvider

    stamps = iter((RECEIPT, RECEIPT + timedelta(milliseconds=2),
                   RECEIPT + timedelta(milliseconds=3)))
    events = forward_events(
        ["INFY"],
        quote_fn=lambda *_: {"NSE:INFY": {
            "timestamp": "2026-11-12T09:30:00+05:30", "last_price": 1500,
        }},
        clock_provider=SessionClockProvider(lambda: next(stamps)),
    )
    event, = events
    assert event.received_utc == RECEIPT
    assert event.available_utc == RECEIPT + timedelta(milliseconds=2)
    assert event.ingested_utc == RECEIPT + timedelta(milliseconds=3)
