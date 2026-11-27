from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.market_quality import (
    MarketQualityError,
    build_instrument_day_quality_report,
)
from financial_brain.forward_edge.quote_archive import QuoteArchive, QuoteArchiveError

AS_OF = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)
SESSION_DAY = AS_OF.date()


def _quote(event_id: str, *, received_offset_ms: int = 250, available_offset_ms: int = 500) -> MarketEvent:
    event_time = AS_OF - timedelta(seconds=1)
    received = event_time + timedelta(milliseconds=received_offset_ms)
    available = event_time + timedelta(milliseconds=available_offset_ms)
    payload = {
        "bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 10,
        "volume": 100, "session_phase": "CONTINUOUS",
        "depth": {"buy": [[100.0, 10]], "sell": [[100.1, 10]]},
    }
    return MarketEvent(event_id, EventKind.QUOTE, "NSE", "NSE:INFY", event_time,
                       received, available, payload, "KITE_CONNECT_READ_ONLY", evidence_id="a" * 64)


def _tick() -> MarketEvent:
    event_time = AS_OF - timedelta(seconds=2)
    received = event_time + timedelta(milliseconds=1_500)
    return MarketEvent("tick-1", EventKind.TICK, "NSE", "NSE:INFY", event_time,
                       received, received, {"price": 100.0}, "KITE_CONNECT_READ_ONLY", evidence_id="b" * 64)


def test_archive_quality_report_measures_feed_and_selection_inputs(tmp_path: Path) -> None:
    archive = QuoteArchive(tmp_path / "quality.sqlite3")
    try:
        quote, tick = _quote("quote-1"), _tick()
        archive.append(quote)
        archive.append(tick)
        report = archive.instrument_day_quality_report(
            venue="NSE", instrument_id="NSE:INFY", session_day=SESSION_DAY,
            as_of=AS_OF, expected_event_count=2, max_lateness_ms=1_000,
            max_quote_age_ms=5_000,
            strike_selection_evidence=({"target_strike": "100", "selected_strike": "105",
                                        "source_sha256": "c" * 64},),
            require_strike_selection=True,
        )
        assert report["status"] == "COMPLETE"
        assert report["event_coverage_fraction"] == 1
        assert report["late_tick_rate"] == 1
        assert report["executable_quote_fraction"] == 1
        assert report["quote_age_ms_max"] == 1_000
        assert report["strike_selection_error"]["mean_absolute_error"] == 5
        assert report["inconsistent_ohlc_count"] == 0
        assert len(report["report_sha256"]) == 64
    finally:
        archive.close()


def test_missing_denominators_and_required_strike_evidence_are_not_reported_as_zero() -> None:
    report = build_instrument_day_quality_report(
        [_quote("quote-1")], venue="NSE", instrument_id="NSE:INFY", session_day=SESSION_DAY,
        as_of=AS_OF, expected_event_count=None, require_strike_selection=True,
    )
    assert report["status"] == "INCOMPLETE"
    assert report["event_coverage_fraction"] is None
    assert report["strike_selection_error"]["status"] == "REQUIRED_MISSING"
    assert report["strike_selection_error"]["mean_absolute_error"] is None


def test_invalid_ohlc_payload_is_counted_instead_of_treated_as_market_data() -> None:
    bar = MarketEvent(
        "bar-1", EventKind.BAR, "NSE", "NSE:INFY", AS_OF, AS_OF, AS_OF,
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10},
        "KITE_CONNECT_READ_ONLY", evidence_id="d" * 64,
    )
    bar.payload["high"] = 100.5
    report = build_instrument_day_quality_report(
        [bar], venue="NSE", instrument_id="NSE:INFY", session_day=SESSION_DAY,
        as_of=AS_OF, expected_event_count=1,
    )
    assert report["status"] == "INCOMPLETE"
    assert report["inconsistent_ohlc_count"] == 1
    assert "INVALID_OR_MUTATED_EVENT_PAYLOAD" in report["issues"]


def test_archive_revalidates_mutable_event_payload_before_persisting(tmp_path: Path) -> None:
    bar = MarketEvent(
        "mutable-bar", EventKind.BAR, "NSE", "NSE:INFY", AS_OF, AS_OF, AS_OF,
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10},
        "KITE_CONNECT_READ_ONLY", evidence_id="e" * 64,
    )
    bar.payload["high"] = 100.5
    archive = QuoteArchive(tmp_path / "mutable.sqlite3")
    try:
        with pytest.raises(QuoteArchiveError, match="changed or became invalid"):
            archive.append(bar)
        assert archive.count() == 0
    finally:
        archive.close()


def test_strike_selection_rows_require_source_hash_and_positive_prices() -> None:
    with pytest.raises(MarketQualityError, match="source SHA-256"):
        build_instrument_day_quality_report(
            [_quote("quote-1")], venue="NSE", instrument_id="NSE:INFY", session_day=SESSION_DAY,
            as_of=AS_OF, expected_event_count=1,
            strike_selection_evidence=({"target_strike": "100", "selected_strike": "0",
                                        "source_sha256": "bad"},),
        )
