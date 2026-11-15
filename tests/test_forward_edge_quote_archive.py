"""FE-0036/37 append-only archive and deterministic quote health calculations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive, QuoteArchiveError

T0 = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _quote(seq: int, *, event_id: str | None = None, event_lag_ms: int = 10) -> MarketEvent:
    received = T0 + timedelta(seconds=seq)
    return MarketEvent(event_id or f"q-{seq}", EventKind.QUOTE, "NSE", "NSE:INFY",
                       received - timedelta(milliseconds=event_lag_ms), received, received,
                       {"bid": 100 + seq, "ask": 101 + seq, "bid_quantity": 10, "ask_quantity": 12},
                       "kite-fixture", sequence=seq, evidence_id="fixture:test")


def test_archive_is_append_only_idempotent_and_roundtrips_provenance(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        first = _quote(1)
        assert archive.append(first)
        assert not archive.append(first)
        assert archive.get(first.event_id) == first
        assert archive.count() == 1
        assert archive.rows()[0]["source"] == "kite-fixture"
        with pytest.raises(QuoteArchiveError, match="different content"):
            archive.append(_quote(2, event_id=first.event_id))
        assert archive.count() == 1
    finally:
        archive.close()


def test_feed_health_reports_sequence_gaps_and_transport_lag(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        archive.append(_quote(1, event_lag_ms=10))
        archive.append(_quote(4, event_lag_ms=30))
        health = archive.health(venue="NSE", instrument_id="NSE:INFY", as_of=T0 + timedelta(seconds=10))
        assert health.received == 2
        assert health.sequence_gaps == 2
        assert health.late_events == 0
        assert health.max_transport_lag_ms == pytest.approx(30)
        assert health.last_received_at == T0 + timedelta(seconds=4)
    finally:
        archive.close()


def test_archive_recovers_rows_after_close_and_reopen(tmp_path) -> None:
    path = tmp_path / "quotes.sqlite3"
    first = QuoteArchive(path)
    first.append(_quote(7))
    first.close()
    second = QuoteArchive(path)
    try:
        assert second.count() == 1
        assert second.get("q-7").sequence == 7
    finally:
        second.close()


def test_decision_snapshot_excludes_late_data_and_is_reproducible(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        archive.append(_quote(1))
        archive.append(_quote(2))
        decision_time = T0 + timedelta(seconds=1, milliseconds=500)
        snapshot = archive.snapshot(decision_id="decision-1", venue="NSE", instrument_id="NSE:INFY",
                                   decision_time=decision_time)
        assert [row["event_id"] for row in snapshot["events"]] == ["q-1"]
        assert snapshot["snapshot_sha256"] == archive.snapshot(
            decision_id="decision-1", venue="NSE", instrument_id="NSE:INFY",
            decision_time=decision_time)["snapshot_sha256"]
        assert len(snapshot["snapshot_sha256"]) == 64
        with pytest.raises(QuoteArchiveError, match="no archived events"):
            archive.snapshot(decision_id="too-early", venue="NSE", instrument_id="NSE:INFY",
                             decision_time=T0 - timedelta(seconds=1))
    finally:
        archive.close()
