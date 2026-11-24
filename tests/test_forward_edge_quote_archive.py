"""FE-0036/37 append-only archive and deterministic quote health calculations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.clock import FixedClock
from financial_brain.forward_edge.quote_archive import QuoteArchive, QuoteArchiveError
from financial_brain.forward_edge.data_distribution import PriceWindow, DistributionThresholds, compare_windows

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


def test_ingest_timestamp_is_injected_immutable_and_not_part_of_source_event_identity(tmp_path) -> None:
    path = tmp_path / "quotes.sqlite3"
    ingested = T0 + timedelta(minutes=1)
    archive = QuoteArchive(path, clock=FixedClock(ingested))
    try:
        event = _quote(1)
        assert archive.append(event)
        assert archive.ingest_time(event.event_id) == ingested
        assert not archive.append(event, ingested_at=ingested + timedelta(minutes=1))
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


def test_provider_windows_from_archive_separate_feed_shift_from_price_window(tmp_path):
    archive = QuoteArchive(tmp_path / "distribution.sqlite3", clock=FixedClock(T0 + timedelta(days=2)))
    reference_start = T0
    reference_end = T0 + timedelta(minutes=3)
    current_start = T0 + timedelta(days=1)
    current_end = current_start + timedelta(minutes=3)

    def append_window(start, *, current=False):
        rows = []
        for index in range(3):
            event_offset = (1, 0, 2)[index] if current else index
            source = ("EXCHANGE" if index in (1, 2) else "KITE") if current else "KITE"
            event_at = start + timedelta(minutes=event_offset)
            received = (start + timedelta(seconds=(61, 62, 121)[index]) if current
                        else event_at + timedelta(seconds=1))
            event = MarketEvent(
                f"{start.date()}:{index}", EventKind.QUOTE, "NSE", "NSE:INFY",
                event_at, received, received,
                {"bid": 100.0 + index, "ask": 100.1 + index,
                 "bid_quantity": 10, "ask_quantity": 10},
                source, sequence=index + 1,
            )
            archive.append(event)
            rows.append(event)
        return rows

    try:
        append_window(reference_start)
        append_window(current_start, current=True)
        reference = archive.provider_window(
            start=reference_start, end=reference_end, as_of=reference_end + timedelta(hours=1),
            expected_quote_observations=3, max_transport_lag=timedelta(seconds=5),
        )
        current = archive.provider_window(
            start=current_start, end=current_end, as_of=current_end + timedelta(hours=1),
            expected_quote_observations=3, max_transport_lag=timedelta(seconds=5),
        )
        result = compare_windows(
            PriceWindow(reference_start, reference_end, 30, 0.01, 0.0),
            PriceWindow(current_start, current_end, 30, 0.01, 0.0),
            reference, current, thresholds=DistributionThresholds(minimum_observations=2),
        )
        assert reference.quote_coverage == current.quote_coverage == 1.0
        assert reference.stale_fraction == 0.0
        assert current.stale_fraction == pytest.approx(1 / 3)
        assert current.out_of_order_fraction == pytest.approx(0.5)
        assert dict(current.source_mix)["EXCHANGE"] == pytest.approx(2 / 3)
        assert dict(current.source_mix)["KITE"] == pytest.approx(1 / 3)
        assert result.status == "PROVIDER_DISTRIBUTION_SHIFT"
        assert not result.price_regime_shift and result.provider_distribution_shift

        outage_start = T0 + timedelta(days=2)
        outage_end = outage_start + timedelta(minutes=3)
        empty = archive.provider_window(
            start=outage_start, end=outage_end, as_of=outage_end + timedelta(hours=1),
            expected_quote_observations=3, max_transport_lag=timedelta(seconds=5),
        )
        assert empty.observations == 0 and empty.quote_coverage == 0
        outage = compare_windows(
            PriceWindow(reference_start, reference_end, 30, 0.01, 0.0),
            PriceWindow(outage_start, outage_end, 30, 0.01, 0.0),
            reference, empty, thresholds=DistributionThresholds(minimum_observations=2),
        )
        assert outage.status == "PROVIDER_DISTRIBUTION_SHIFT"
        assert outage.provider_reasons == ("PROVIDER_WINDOW_EMPTY",)
    finally:
        archive.close()


def test_archive_price_window_and_runner_report_pair_price_and_feed_diagnostics(tmp_path):
    from financial_brain.forward_edge.session_runner import ForwardSessionRunner
    from financial_brain.forward_edge.session_state import ForwardSessionStore

    archive = QuoteArchive(tmp_path / "paired-windows.sqlite3", clock=FixedClock(T0 + timedelta(days=2)))
    runner = ForwardSessionRunner(ForwardSessionStore(tmp_path / "state.sqlite3"),
                                  tmp_path / "runner.sqlite3")
    reference_start, current_start = T0, T0 + timedelta(days=1)
    duration = timedelta(minutes=3)
    as_of = current_start + duration + timedelta(hours=1)
    try:
        for start, mids in ((reference_start, (100.0, 100.1, 100.2)),
                            (current_start, (100.0, 102.0, 104.0))):
            for index, mid in enumerate(mids):
                event_at = start + timedelta(minutes=index)
                event = MarketEvent(
                    f"paired:{start.date()}:{index}", EventKind.QUOTE, "NSE", "NSE:INFY",
                    event_at, event_at + timedelta(seconds=1), event_at + timedelta(seconds=1),
                    {"bid": mid - 0.05, "ask": mid + 0.05}, "KITE", sequence=index + 1,
                )
                archive.append(event)
        reference_price = archive.price_window(
            start=reference_start, end=reference_start + duration, as_of=as_of,
            venue="NSE", instrument_id="NSE:INFY")
        assert reference_price.observations == 3
        assert reference_price.net_return == pytest.approx(0.002)
        assert reference_price.realized_volatility > 0

        report = runner.compare_archive_windows(
            archive, venue="NSE", instrument_id="NSE:INFY",
            reference_start=reference_start, reference_end=reference_start + duration,
            current_start=current_start, current_end=current_start + duration, as_of=as_of,
            expected_reference_quotes=3, expected_current_quotes=3,
            max_transport_lag=timedelta(seconds=5),
            thresholds=DistributionThresholds(minimum_observations=2),
        )
        assert report["comparison"]["price_regime_shift"] is True
        assert report["comparison"]["provider_distribution_shift"] is False
        assert report["trading_action"] == "NONE_DIAGNOSTIC_ONLY"
    finally:
        runner.close()
        archive.close()


def test_archive_price_window_rejects_future_cutoff_and_insufficient_quotes(tmp_path):
    archive = QuoteArchive(tmp_path / "price-cutoff.sqlite3")
    try:
        event = _quote(1)
        archive.append(event)
        with pytest.raises(QuoteArchiveError, match="cutoff"):
            archive.price_window(start=T0, end=T0 + timedelta(minutes=1),
                                as_of=T0 + timedelta(seconds=30), venue="NSE",
                                instrument_id="NSE:INFY")
        with pytest.raises(QuoteArchiveError, match="at least two"):
            archive.price_window(start=T0, end=T0 + timedelta(minutes=1),
                                as_of=T0 + timedelta(hours=1), venue="NSE",
                                instrument_id="NSE:INFY")
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
