"""Read-only Kite quote adapter contracts; no network or credentials required."""
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_data import (
    DataError, FeedHealth, decision_visible, executable_quote, quote_quality_reasons,
)
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner
from financial_brain.forward_edge.source_capture_start import CaptureStartReceipt, SourceEntitlement
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


def test_kite_capture_start_receipt_is_persisted_before_quote_request(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "capture-session.jsonl")
    config = ForwardRunConfig(
        session_id="kite-capture-session", exchange_date="2026-11-12", cohort_id="cohort-1",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64, constraints_sha256="c" * 64,
        ledger_id="ledger-1", benchmark_id="NIFTY50", instruments=("NSE:INFY",),
        capital_inr="100000", mode=Mode.LIVE_PAPER,
        authority_strategy_id="momentum", authority_account_id="paper-1",
    )
    capture_receipt = CaptureStartReceipt(
        capture_id="kite-capture-1", capture_started_at=RECEIPT - timedelta(seconds=2),
        earliest_decision_at=RECEIPT + timedelta(minutes=1), clock_skew_ms=12,
        clock_evidence_id="clock:local-probe", quote_health=FeedHealth(None, 0, 0, 0),
        entitlements=(SourceEntitlement("kite-quotes", "Kite Connect", True,
                                        "entitlement:operator", RECEIPT + timedelta(hours=1)),),
    )

    def quote(keys, kind):
        rows = DurableJournal(runner.journal_path).records()
        assert any(row.get("t") == "SOURCE_CAPTURE_START" for row in rows)
        return {"NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "last_price": 1500.5}}

    events = forward_events(["INFY"], quote_fn=quote, received_at=RECEIPT,
                            capture_runner=runner, capture_config=config,
                            capture_receipt=capture_receipt)
    runner.append_events(config, events)
    records = DurableJournal(runner.journal_path).records()
    marker_i = next(i for i, row in enumerate(records) if row.get("t") == "SOURCE_CAPTURE_START")
    market_i = next(i for i, row in enumerate(records) if row.get("t") == "MARKET_EVENT")
    assert marker_i < market_i


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
    assert dict(event.payload)["quote_fidelity"] == "CROSSED_BOOK"
    assert executable_quote(event) == (False, "CROSSED_BOOK")


def test_locked_book_is_not_fill_capable():
    event, = forward_events(["INFY"], quote_fn=lambda *_: {
        "NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "depth": {
            "buy": [{"price": 100, "quantity": 5}], "sell": [{"price": 100, "quantity": 5}]}}
    }, received_at=RECEIPT)
    assert dict(event.payload)["quote_fidelity"] == "LOCKED_BOOK"
    assert executable_quote(event) == (False, "LOCKED_BOOK")


def test_kite_circuit_limits_are_preserved_and_block_touching_band():
    event, = forward_events(["INFY"], quote_fn=lambda *_: {
        "NSE:INFY": {"timestamp": "2026-11-12 09:29:59", "lower_circuit_limit": 90,
                     "upper_circuit_limit": 100.1, "depth": {
                         "buy": [{"price": 99.9, "quantity": 5}],
                         "sell": [{"price": 100.1, "quantity": 5}]}}
    }, received_at=RECEIPT)
    payload = dict(event.payload)
    assert payload["circuit_lower"] == "90"
    assert payload["circuit_upper"] == "100.1"
    assert quote_quality_reasons(event) == ["CIRCUIT_BAND"]


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
