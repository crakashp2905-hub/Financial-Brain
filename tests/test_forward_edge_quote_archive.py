"""FE-0036/37 append-only archive and deterministic quote health calculations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.clock import FixedClock
from financial_brain.forward_edge.quote_archive import QuoteArchive, QuoteArchiveError

T0 = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _quote(seq: int, *, event_id: str | None = None, event_lag_ms: int = 10,
           bid: float | None = None, venue: str = "NSE", instrument_id: str = "NSE:INFY",
           source: str = "kite-fixture", source_at: datetime | None = None,
           revision_of: str | None = None) -> MarketEvent:
    received = T0 + timedelta(seconds=seq)
    return MarketEvent(event_id or f"q-{seq}", EventKind.QUOTE, venue, instrument_id,
                       received - timedelta(milliseconds=event_lag_ms), received, received,
                       {"bid": bid if bid is not None else 100 + seq, "ask": 101 + seq,
                        "bid_quantity": 10, "ask_quantity": 12},
                       source, sequence=seq, evidence_id="fixture:test", source_at=source_at,
                       revision_of=revision_of)


def test_archive_is_append_only_idempotent_and_roundtrips_provenance(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        first = _quote(1)
        assert archive.append(first)
        assert not archive.append(first)
        assert archive.get(first.event_id) == first
        assert archive.count() == 1
        assert archive.rows()[0]["source"] == "kite-fixture"
        # Same provider ID with a new source clock/payload is retained as another record;
        # the archive never silently replaces the original row.
        revised = _quote(2, event_id=first.event_id)
        assert archive.append(revised)
        assert archive.count() == 2
        with pytest.raises(QuoteArchiveError, match="ambiguous"):
            archive.get(first.event_id)
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


def test_ingest_timestamp_is_injected_immutable_and_not_part_of_source_event_identity(tmp_path) -> None:
    path = tmp_path / "quotes.sqlite3"
    ingested = T0 + timedelta(minutes=1)
    archive = QuoteArchive(path, clock=FixedClock(ingested))
    try:
        event = _quote(1)
        assert archive.append(event)
        assert archive.ingest_time(event.event_id) == ingested
        redelivery = MarketEvent(event.event_id, event.kind, event.venue, event.instrument_id,
                                 event.event_time, event.received_at + timedelta(seconds=2),
                                 event.available_at + timedelta(seconds=2), event.payload, event.source,
                                 sequence=event.sequence, evidence_id=event.evidence_id)
        assert not archive.append(redelivery, ingested_at=ingested + timedelta(minutes=1))
        assert archive.ingest_time(event.event_id) == ingested
    finally:
        archive.close()


def test_legacy_archive_migration_does_not_invent_prior_ingest_times(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    ingested = T0 + timedelta(minutes=1)
    legacy = _quote(8)
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE market_events (
            event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, venue TEXT NOT NULL,
            instrument_id TEXT NOT NULL, event_time TEXT NOT NULL, received_at TEXT NOT NULL,
            available_at TEXT NOT NULL, sequence INTEGER, source TEXT NOT NULL,
            evidence_id TEXT, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL)""")
        con.execute("INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [legacy.event_id, legacy.kind.value, legacy.venue, legacy.instrument_id,
                     legacy.event_time.isoformat(), legacy.received_at.isoformat(),
                     legacy.available_at.isoformat(), legacy.sequence, legacy.source,
                     legacy.evidence_id, legacy.sha256(),
                     json.dumps(legacy.as_dict(), sort_keys=True, separators=(",", ":"))])
    archive = QuoteArchive(path, clock=FixedClock(ingested))
    try:
        assert archive.ingest_time("q-8") is None
        assert archive.append(_quote(9))
        assert archive.ingest_time("q-9") == ingested
        columns = {row[1] for row in archive._con.execute("PRAGMA table_info(market_events)")}
        assert {"record_key", "dedupe_sha256"} <= columns
    finally:
        archive.close()


def test_archive_preserves_source_corrections_as_append_only_revisions(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        original = _quote(1, event_id="bar-1", source_at=T0)
        correction = _quote(2, event_id="bar-1-correction", bid=99.5,
                            source_at=T0, revision_of="bar-1")
        assert archive.append(original)
        assert archive.append(correction)
        assert archive.count() == 2
        assert archive.get("bar-1").payload["bid"] == 101
        assert archive.get(correction.event_id).revision_of == "bar-1"
        assert not archive.append(correction)
        orphan = _quote(3, event_id="orphan-revision", revision_of="missing-parent")
        with pytest.raises(QuoteArchiveError, match="parent must exist"):
            archive.append(orphan)
    finally:
        archive.close()


def test_archive_deduplicates_and_preserves_corrected_candles(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "candles.sqlite3")
    try:
        def bar(event_id: str, *, close: float, revision_of: str | None = None) -> MarketEvent:
            return MarketEvent(
                event_id=event_id, kind=EventKind.BAR, venue="NSE", instrument_id="NSE:INFY",
                event_time=T0, received_at=T0 + timedelta(seconds=2),
                available_at=T0 + timedelta(seconds=2), payload={
                    "open": 100, "high": max(102, close), "low": 99,
                    "close": close, "volume": 10,
                }, source="exchange-fixture", source_at=T0, revision_of=revision_of,
            )

        original = bar("bar-1", close=101)
        redelivery = bar("bar-1", close=101)
        correction = bar("bar-1-r1", close=101.5, revision_of="bar-1")
        assert archive.append(original)
        assert not archive.append(redelivery)
        assert archive.append(correction)
        assert archive.count() == 2
        assert archive.get("bar-1-r1").payload["close"] == 101.5
    finally:
        archive.close()


def test_archive_scopes_duplicate_event_ids_by_source_identity_and_keeps_late_rows(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        early = _quote(4, event_id="same-id", source_at=T0 + timedelta(seconds=4))
        late_arriving_old_record = _quote(3, event_id="same-id", source_at=T0 + timedelta(seconds=3))
        other_venue = _quote(4, event_id="same-id", venue="BSE", source="bse-fixture",
                             source_at=T0 + timedelta(seconds=4))
        assert archive.append(early)
        assert archive.append(late_arriving_old_record)
        assert archive.append(other_venue)
        assert archive.count() == 3
        ambiguous_revision = _quote(5, event_id="ambiguous-r1", revision_of="same-id")
        with pytest.raises(QuoteArchiveError, match="parent event ID is ambiguous"):
            archive.append(ambiguous_revision)
        assert {event.venue for event in archive.events_for_session(
            datetime(2026, 11, 15).date())} == {"NSE", "BSE"}
    finally:
        archive.close()


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




def test_coverage_report_uses_explicit_denominators_and_labels_fixture_sources(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        archive.append(_quote(1))
        report = archive.coverage(expected_sessions={"2026-11-15", "2026-11-16"},
                                  intents={("2026-11-15", "NSE:INFY"): 1000,
                                           ("2026-11-16", "NSE:RELIANCE"): 3000},
                                  as_of=T0 + timedelta(seconds=10))
        assert report.expected_sessions == 2 and report.covered_sessions == 1
        assert report.expected_names == 2 and report.covered_names == 1
        assert report.requested_intent_notional == 4000
        assert report.covered_intent_notional == 1000
        assert report.quote_coverage_fraction == pytest.approx(0.25)
        assert report.depth_coverage_fraction == 0
        assert report.claim_scope == "FIXTURE_OR_MIXED"
        assert "2026-11-16:NSE:RELIANCE:QUOTE" in report.missing_keys
    finally:
        archive.close()


def test_coverage_rejects_intents_outside_declared_sessions_and_naive_clock(tmp_path) -> None:
    archive = QuoteArchive(tmp_path / "quotes.sqlite3")
    try:
        with pytest.raises(ValueError, match="not in expected_sessions"):
            archive.coverage(expected_sessions={"s1"}, intents={("s2", "NSE:INFY"): 100}, as_of=T0)
        with pytest.raises(ValueError, match="timezone aware"):
            archive.coverage(expected_sessions={"s1"}, intents={("s1", "NSE:INFY"): 100},
                             as_of=datetime(2026, 11, 15))
    finally:
        archive.close()
