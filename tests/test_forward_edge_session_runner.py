from datetime import date, datetime, time, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import duckdb
import pytest

from financial_brain.forward_edge.market_data import normalize_kite_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.instrument_rules import fixture_rule_book
from financial_brain.forward_edge.session_runner import (
    ForwardSessionRunner,
    ForwardSessionRunnerError,
)
from financial_brain.forward_edge.paper_book_adapter import (
    PaperBookSignal,
    due_exits,
    pending_signals,
    reconcile_exit_receipt,
    record_signal_decision,
    record_exit_decision,
    reconcile_entry_receipt,
)
from financial_brain.forward_edge.strategy_adapter import CandidateSignal
from financial_brain.forward_edge.strategy_pipeline import execute_candidate
from financial_brain.kernel.registry import StrategyEligibility, StrategySpec
from financial_brain.kernel.units import BasisPoints, Price, WholeUnits
from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
)
from financial_brain.forward_edge.venue_calendar import Session as VenueSession, VenueCalendar


NOW = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)
READY_PREFLIGHT = {name: True for name in (
    "calendar", "credentials", "disk", "feeds", "data_contracts", "risk", "system_health",
    "broker_disabled",
)}


def make_config(*, max_quote_age_ms=None):
    constraints = {"max_positions": 1, "session_phase": "CONTINUOUS",
                   "allow_synthetic_fixture_events": True,
                   "instrument_rules": fixture_rule_book("NSE:INFY"),
                   "data_permissions": [{"source_id": "KITE_CONNECT_READ_ONLY",
                                        "use": "PAPER_SIMULATION", "status": "USE_ALLOWED",
                                        "effective_from": "2026-11-13", "effective_through": None,
                                        "evidence_ref": "tests/fixture-kite-permission",
                                        "reviewer": "test-fixture"}]}
    if max_quote_age_ms is not None:
        constraints["max_quote_age_ms"] = max_quote_age_ms
    return ForwardRunConfig(date(2026, 11, 13), "cohort-v1", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"), constraints, "ledger-1")


def make_config_with_constraints(constraints):
    constraints = {"session_phase": "CONTINUOUS",
                   "allow_synthetic_fixture_events": True,
                   "data_permissions": [{"source_id": "KITE_CONNECT_READ_ONLY",
                                        "use": "PAPER_SIMULATION", "status": "USE_ALLOWED",
                                        "effective_from": "2026-11-13", "effective_through": None,
                                        "evidence_ref": "tests/fixture-kite-permission",
                                        "reviewer": "test-fixture"}],
                   "instrument_rules": fixture_rule_book("NSE:INFY"), **constraints}
    return ForwardRunConfig(date(2026, 11, 13), "cohort-v1", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"), constraints, "ledger-1")


def make_event(*, event_id="fixture-1", available_at=NOW, event_time=NOW):
    return MarketEvent(event_id, EventKind.QUOTE, "NSE", "NSE:INFY", event_time,
                       available_at, available_at, {"bid": 100.0, "ask": 100.1}, "FIXTURE")


def make_runner(tmp_path):
    return ForwardSessionRunner(ForwardSessionStore(tmp_path / "state.sqlite"), tmp_path / "journal.sqlite")


def opened(runner, session_id="s", config=None):
    return runner.begin(session_id, config or make_config(), now=NOW,
                        preflight={**READY_PREFLIGHT, "data_rights_configured": True,
                                   "risk_policy_loaded": True})


def test_fixture_and_kite_adapters_enter_the_same_normalized_runner(tmp_path):
    runner = make_runner(tmp_path)
    assert opened(runner, "fixture-session") is SessionState.OPEN
    fixture = make_event()
    assert runner.ingest("fixture-session", make_config(), [fixture]) == {
        "received": 1, "persisted": 1, "duplicate": 0}

    assert opened(runner, "kite-session") is SessionState.OPEN
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.05, "volume": 100,
             "depth": {"buy": [{"price": 100.0, "quantity": 50}],
                       "sell": [{"price": 100.1, "quantity": 40}]}}
    observed = normalize_kite_quote("NSE:INFY", quote, received_at=NOW)
    from financial_brain.forward_edge.quote_archive import QuoteArchive
    archive = QuoteArchive(tmp_path / "kite-events.sqlite")
    archive.append(observed)
    prior_day = quote | {"timestamp": "2026-11-12 10:15:00"}
    archive.append(normalize_kite_quote("NSE:INFY", prior_day, received_at=NOW))
    archived_at = archive.ingest_time(observed.event_id)
    assert runner.ingest_kite_archive("kite-session", make_config(), archive)["persisted"] == 1
    records = runner.records("kite-session")
    assert records["events"][0]["source"] == "KITE_CONNECT_READ_ONLY"
    assert records["events"][0]["evidence_id"] == observed.evidence_id
    assert records["events"][0]["ingested_at"] == archived_at.isoformat()
    assert records["events"][0]["ingest_clock_source"] == "QUOTE_ARCHIVE"
    archive.close()
    runner.close()


def test_event_offsets_resume_after_restart_without_replaying_acknowledged_prefix(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    events = [make_event(event_id=f"offset-{index}", available_at=NOW + timedelta(seconds=index),
                         event_time=NOW + timedelta(seconds=index)) for index in range(3)]
    assert runner.ingest("s", config, events) == {"received": 3, "persisted": 3, "duplicate": 0}

    batch = runner.read_events_after("s", "strategy:momentum", limit=2)
    assert [row["event_id"] for row in batch] == ["offset-0", "offset-1"]
    with pytest.raises(ForwardSessionRunnerError, match="exact persisted prefix"):
        runner.acknowledge_event_offset("s", "strategy:momentum", expected_offset=0,
                                        through_offset=batch[1]["offset"],
                                        event_ids=("offset-0",))

    committed = runner.acknowledge_event_offset(
        "s", "strategy:momentum", expected_offset=0, through_offset=batch[1]["offset"],
        event_ids=("offset-0", "offset-1"))
    retried = runner.acknowledge_event_offset(
        "s", "strategy:momentum", expected_offset=0, through_offset=batch[1]["offset"],
        event_ids=("offset-0", "offset-1"))
    assert retried["idempotent"] is True
    assert retried["cursor_sha256"] == committed["cursor_sha256"]
    runner.close()

    resumed = make_runner(tmp_path)
    assert resumed.event_cursor("s", "strategy:momentum")["event_offset"] == batch[1]["offset"]
    remaining = resumed.read_events_after("s", "strategy:momentum")
    assert [row["event_id"] for row in remaining] == ["offset-2"]
    resumed.acknowledge_event_offset(
        "s", "strategy:momentum", expected_offset=batch[1]["offset"],
        through_offset=remaining[0]["offset"], event_ids=("offset-2",))
    assert resumed.read_events_after("s", "strategy:momentum") == []
    resumed.close()


def test_session_heartbeat_snapshot_tracks_live_producers_and_missing_components(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event(event_id="heartbeat-quote")
    runner.ingest("s", config, [event])

    persisted = runner.records("s")["component_heartbeats"]
    assert persisted["components"]["quote_feed"]["status"] == "RECORDED"
    assert persisted["components"]["quote_feed"]["last_good_event_id"] == event.event_id
    assert persisted["components"]["ledger_writer"]["status"] == "RECORDED"
    health = runner.heartbeat_health("s", now=runner.clock.now())
    assert health["entry_readiness"] == "NO_ENTRY"
    assert {"completed_bars", "strategy_process", "clock_sync", "position_monitor"} <= set(
        health["missing_or_stale"])
    runner.close()


def test_nonfixture_kite_data_requires_current_purpose_scoped_permission(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config_with_constraints({"data_permissions": [], "allow_synthetic_fixture_events": False})
    opened(runner, config=config)
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.05, "volume": 100,
             "depth": {"buy": [{"price": 100.0, "quantity": 50}],
                       "sell": [{"price": 100.1, "quantity": 40}]}}
    observed = normalize_kite_quote("NSE:INFY", quote, received_at=NOW)
    with pytest.raises(ForwardSessionRunnerError, match="rights are unknown"):
        runner.ingest("s", config, [observed])
    assert runner.records("s")["events"] == []
    assert runner.records("s")["data_rejections"]
    runner.close()


def test_session_records_publish_per_signal_timing_telemetry_and_missing_stages(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event(event_id="timed-signal")
    runner.ingest("s", config, [event])
    feature_ready = NOW + timedelta(milliseconds=5)
    decided = NOW + timedelta(milliseconds=10)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="timed-decision",
                  outcome="TRADE", input_event_ids=(event.event_id,), decided_at=decided,
                  feature_ready_at=feature_ready)
    approved = decided + timedelta(milliseconds=2)
    runner.record_risk_verdict(session_id="s", decision_id="timed-decision", verdict_id="risk-timed",
                               allowed=True, policy_id="timing-v1", policy_sha256="a" * 64,
                               assessed_at=approved)

    telemetry = runner.records("s")["timing_telemetry"]
    assert telemetry["schema"] == "fb.forward_session_timing_telemetry.v1"
    row = telemetry["signals"][0]
    assert row["feature_ready_at"] == feature_ready.isoformat()
    assert row["signal_decision_at"] == decided.isoformat()
    assert row["oms_risk_assessed_at"] == approved.isoformat()
    assert row["source_clocks"][0]["received_at"] == event.received_at.isoformat()
    assert row["status"] == "INCOMPLETE"
    assert "SIMULATED_FILL_TIMESTAMP" in row["missing_stages"]
    assert "RISK_STOP_TRIGGER_TIMESTAMP" in row["missing_stages"]
    assert "EXIT_ROLE_OR_EXECUTION_TIMESTAMP" in row["missing_stages"]
    runner.close()


def test_feature_ready_clock_must_follow_inputs_and_precede_decision(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event(event_id="feature-ready-input")
    runner.ingest("s", config, [event])
    decided = NOW + timedelta(milliseconds=10)
    with pytest.raises(ForwardSessionRunnerError, match="follow all input availability"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="bad-feature-clock",
                      outcome="NO_TRADE", input_event_ids=(event.event_id,), decided_at=decided,
                      reason_codes=("NO_EDGE",), feature_ready_at=NOW - timedelta(milliseconds=1))
    with pytest.raises(ForwardSessionRunnerError, match="follow all input availability"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="late-feature-clock",
                      outcome="NO_TRADE", input_event_ids=(event.event_id,), decided_at=decided,
                      reason_codes=("NO_EDGE",), feature_ready_at=decided + timedelta(milliseconds=1))
    runner.close()


def test_fixture_source_cannot_bypass_rights_gate_without_frozen_fixture_scope(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config_with_constraints({"allow_synthetic_fixture_events": False})
    opened(runner, config=config)
    with pytest.raises(ForwardSessionRunnerError, match="fixture market events require"):
        runner.ingest("s", config, [make_event()])
    assert runner.records("s")["events"] == []
    runner.close()


def test_ingest_requires_availability_to_follow_local_receipt(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner, "available-after-receipt")
    received = NOW
    available = NOW + timedelta(milliseconds=5)
    delayed = MarketEvent("delayed-availability", EventKind.QUOTE, "NSE", "NSE:INFY", NOW,
                          received, available, {"bid": 100.0, "ask": 100.1}, "FIXTURE")
    assert runner.ingest("available-after-receipt", make_config(), [delayed], ingest_metadata={
        delayed.event_id: (available + timedelta(milliseconds=1), "FIXTURE_CLOCK")})["persisted"] == 1

    opened(runner, "availability-before-receipt")
    reversed_clocks = MarketEvent("reversed-availability", EventKind.QUOTE, "NSE", "NSE:INFY", NOW,
                                  received, received - timedelta(milliseconds=1),
                                  {"bid": 100.0, "ask": 100.1}, "FIXTURE")
    with pytest.raises(ForwardSessionRunnerError, match="availability cannot precede local receipt"):
        runner.ingest("availability-before-receipt", make_config(), [reversed_clocks])
    runner.close()


def test_runner_event_schema_migration_adds_ingest_provenance_columns(tmp_path):
    journal = tmp_path / "journal.sqlite"
    with sqlite3.connect(journal) as con:
        con.execute("""CREATE TABLE runner_events (
            session_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
            available_at TEXT NOT NULL, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL,
            PRIMARY KEY(session_id, event_id))""")
    runner = make_runner(tmp_path)
    opened(runner)
    runner.ingest("s", make_config(), [make_event()],
                  ingest_metadata={"fixture-1": (NOW + timedelta(milliseconds=1), "MIGRATION_TEST")})
    event = runner.records("s")["events"][0]
    assert event["ingested_at"] == (NOW + timedelta(milliseconds=1)).isoformat()
    assert event["ingest_clock_source"] == "MIGRATION_TEST"
    runner.close()


def test_preflight_failures_reject_the_session_without_accepting_market_events(tmp_path):
    runner = make_runner(tmp_path)
    state = runner.begin("s", make_config(), now=NOW,
                         preflight={**READY_PREFLIGHT, "feeds": False, "feed_entitlement": False})
    assert state is SessionState.REJECTED
    assert set(runner.records("s")["lifecycle"][0]["checks"]) >= set(READY_PREFLIGHT)
    with pytest.raises(ForwardSessionRunnerError, match="OPEN session"):
        runner.ingest("s", make_config(), [make_event()])
    runner.close()


def test_omitted_required_preflight_checks_are_recorded_as_failures(tmp_path):
    runner = make_runner(tmp_path)
    state = runner.begin("missing-checks", make_config(), now=NOW,
                         preflight={"risk_policy_loaded": True})
    assert state is SessionState.REJECTED
    checks = runner.records("missing-checks")["lifecycle"][0]["checks"]
    assert all(checks[name] is False for name in READY_PREFLIGHT)
    with pytest.raises(ForwardSessionRunnerError, match="OPEN session"):
        runner.ingest("missing-checks", make_config(), [make_event()])
    runner.close()


def _calendar(day, *, session_type="NORMAL", opens=time(9, 15), closes=time(15, 30),
              source_as_of=None):
    row = VenueSession("NSE", day, opens, closes, session_type, "NSE official calendar fixture",
                       "b" * 64, NOW)
    return VenueCalendar((row,), source_as_of=source_as_of or day)


def test_calendar_planning_distinguishes_holiday_from_missing_source_coverage(tmp_path):
    runner = make_runner(tmp_path)
    holiday_calendar = VenueCalendar((), source_as_of=date(2026, 11, 13))
    holiday = runner.plan_calendar_day("holiday", make_config(), venue="NSE",
                                       calendar=holiday_calendar, now=NOW, preflight={})
    assert holiday is SessionState.NO_TRADE
    assert runner.records("holiday")["lifecycle"][0]["reason"] == \
        "no venue session appears in the source-covered calendar"
    assert runner.plan_calendar_day("holiday", make_config(), venue="NSE",
                                    calendar=holiday_calendar, now=NOW + timedelta(seconds=1),
                                    preflight={}) is SessionState.NO_TRADE
    with pytest.raises(ForwardSessionRunnerError, match="matching calendar-plan evidence"):
        runner.plan_calendar_day("holiday", make_config(), venue="NSE",
                                 calendar=_calendar(date(2026, 11, 13)), now=NOW + timedelta(seconds=1),
                                 preflight={})

    uncovered_config = make_config()
    uncovered_config = replace(uncovered_config, exchange_date=date(2026, 11, 14))
    uncovered = runner.plan_calendar_day("uncovered", uncovered_config, venue="NSE",
                                         calendar=holiday_calendar, now=NOW, preflight={})
    assert uncovered is SessionState.INCOMPLETE
    assert runner.records("uncovered")["lifecycle"][0]["outcome"] == "INCOMPLETE"
    runner.close()


def test_special_session_requires_explicit_support_and_enforces_sourced_bounds(tmp_path):
    calendar = _calendar(date(2026, 11, 13), session_type="SPECIAL",
                         opens=time(10, 0), closes=time(13, 0))
    runner = make_runner(tmp_path)
    unsupported = runner.plan_calendar_day("special-off", make_config(), venue="NSE",
                                           calendar=calendar, now=NOW, preflight={})
    assert unsupported is SessionState.NO_TRADE
    assert runner.records("special-off")["lifecycle"][0]["session_type"] == "SPECIAL"

    opened_state = runner.plan_calendar_day(
        "special-on", make_config(), venue="NSE", calendar=calendar, now=NOW,
        preflight={**READY_PREFLIGHT, "special_session_adapter": True}, supported_session_types=frozenset({"SPECIAL"}))
    assert opened_state is SessionState.OPEN
    pre_open = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 09:30:00", "last_price": 100.0, "volume": 100,
        "depth": {"buy": [{"price": 99.9, "quantity": 10}],
                  "sell": [{"price": 100.1, "quantity": 10}]},
    }, received_at=NOW)
    with pytest.raises(ForwardSessionRunnerError, match="outside the sourced venue session"):
        runner.ingest("special-on", make_config(), [pre_open],
                      ingest_metadata={pre_open.event_id: (NOW + timedelta(milliseconds=1), "FIXTURE")})
    runner.close()


def test_planned_session_resumed_after_source_close_becomes_durable_incomplete(tmp_path):
    calendar = _calendar(date(2026, 11, 13))
    runner = make_runner(tmp_path)
    state = runner.plan_calendar_day("planned", make_config(), venue="NSE", calendar=calendar,
                                    now=NOW, preflight={**READY_PREFLIGHT, "risk_policy_loaded": True})
    assert state is SessionState.OPEN
    _, closes = calendar.session_bounds_utc("NSE", date(2026, 11, 13))
    assert runner.resume("planned", now=closes - timedelta(seconds=1))["state"] == "OPEN"
    runner.close()

    resumed = make_runner(tmp_path)
    result = resumed.resume("planned", now=closes + timedelta(seconds=1))
    assert result["state"] == "INCOMPLETE"
    assert result["records"]["session_reports"][0]["status"] == "INCOMPLETE"
    assert "without a durable session close" in result["records"]["session_reports"][0]["reason"]
    assert resumed.resume("planned", now=closes + timedelta(seconds=2))["state"] == "INCOMPLETE"
    resumed.close()


def test_supervisor_marks_stale_feed_incomplete_and_keeps_fresh_feed_open(tmp_path):
    calendar = _calendar(date(2026, 11, 13))
    runner = make_runner(tmp_path)
    for session_id in ("stale", "fresh"):
        assert runner.plan_calendar_day(session_id, make_config(), venue="NSE", calendar=calendar,
                                        now=NOW, preflight={**READY_PREFLIGHT, "risk_policy_loaded": True}) is SessionState.OPEN
    event = make_event(event_id="fresh-quote")
    runner.ingest("fresh", make_config(), [event],
                  ingest_metadata={event.event_id: (NOW, "SUPERVISOR_TEST_CLOCK")})

    active = runner.supervise_open_sessions(now=NOW + timedelta(seconds=30),
                                            max_feed_silence=timedelta(seconds=90))
    assert {row["session_id"]: row["state"] for row in active} == {
        "fresh": "OPEN", "stale": "INCOMPLETE"}
    stale_report = runner.records("stale")["session_reports"][0]
    assert stale_report["status"] == "INCOMPLETE" and "market feed silent" in stale_report["reason"]

    expired = runner.supervise_open_sessions(now=NOW + timedelta(seconds=91),
                                             max_feed_silence=timedelta(seconds=90))
    assert expired == [{"session_id": "fresh", "state": "INCOMPLETE",
                        "reason": "market feed silent for 91.000s; limit is 90.000s",
                        "stale_seconds": 91.0}]
    runner.close()


def test_supervisor_fails_closed_for_open_sessions_without_calendar_plan(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    result = runner.supervise_open_sessions(now=NOW + timedelta(seconds=1))
    assert result[0]["state"] == "INCOMPLETE"
    assert "sourced venue calendar plan is missing" in result[0]["reason"]
    assert runner.records("s")["session_reports"][0]["status"] == "INCOMPLETE"
    runner.close()


def test_events_must_match_frozen_universe_and_exchange_date(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    with pytest.raises(ForwardSessionRunnerError, match="outside frozen universe"):
        runner.ingest("s", make_config(), [MarketEvent(
            "other", EventKind.QUOTE, "NSE", "NSE:OTHER", NOW, NOW, NOW,
            {"bid": 1.0, "ask": 1.1}, "FIXTURE")])
    other_day = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ForwardSessionRunnerError, match="exchange date"):
        runner.ingest("s", make_config(), [make_event(event_id="old", event_time=other_day)])
    runner.close()


def test_rejected_event_batch_rolls_back_earlier_rows_atomically(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    bad = MarketEvent("other", EventKind.QUOTE, "NSE", "NSE:OTHER", NOW, NOW, NOW,
                      {"bid": 1.0, "ask": 1.1}, "FIXTURE")
    with pytest.raises(ForwardSessionRunnerError, match="outside frozen universe"):
        runner.ingest("s", make_config(), [make_event(), bad])
    assert runner.records("s")["events"] == []
    runner.close()


def test_decisions_require_available_inputs_and_are_immutable(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    available = NOW.replace(second=10)
    runner.ingest("s", make_config(), [make_event(available_at=available, event_time=available)])
    with pytest.raises(ForwardSessionRunnerError, match="unavailable at decision time"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="d1", outcome="NO_TRADE",
                      decided_at=NOW, input_event_ids=("fixture-1",), reason_codes=("STALE",))
    decision_time = NOW.replace(second=11)
    assert runner.decide(session_id="s", strategy_id="momentum", decision_id="d1", outcome="NO_TRADE",
                         decided_at=decision_time, input_event_ids=("fixture-1",),
                         reason_codes=("EDGE_BELOW_COST",)) is True
    assert runner.decide(session_id="s", strategy_id="momentum", decision_id="d1", outcome="NO_TRADE",
                         decided_at=decision_time, input_event_ids=("fixture-1",),
                         reason_codes=("EDGE_BELOW_COST",)) is False
    with pytest.raises(ForwardSessionRunnerError, match="conflicting content"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="d1", outcome="NO_TRADE",
                      decided_at=decision_time, input_event_ids=("fixture-1",), reason_codes=("OTHER",))
    with pytest.raises(ForwardSessionRunnerError, match="clock cannot move backwards"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="d2", outcome="NO_TRADE",
                      decided_at=decision_time - timedelta(milliseconds=1), input_event_ids=("fixture-1",),
                      reason_codes=("REORDERED_CLOCK",))
    runner.close()


def test_restart_restores_open_session_and_duplicate_events_do_not_duplicate_rows(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    event = make_event()
    runner.ingest("s", make_config(), [event])
    runner.close()

    resumed = make_runner(tmp_path)
    assert opened(resumed) is SessionState.OPEN
    assert resumed.ingest("s", make_config(), [event]) == {"received": 1, "persisted": 0, "duplicate": 1}
    assert len(resumed.records("s")["events"]) == 1
    resumed.close()


def test_explicit_resume_rehydrates_open_session_config_and_event_manifest(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    event = make_event()
    runner.ingest("s", make_config(), [event])
    original = runner.records("s")
    runner.close()

    resumed = make_runner(tmp_path)
    result = resumed.resume("s")
    assert result["state"] == "OPEN"
    assert result["config_sha256"] == make_config().sha256()
    assert result["records"] == original
    assert result["unresolved_paper_outbox_keys"] == []
    assert result["physical_orders_enabled"] is False
    resumed.close()


def test_resume_finishes_a_crash_interrupted_drain_only_when_close_evidence_is_complete(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    runner.state_store.transition("s", SessionState.DRAINING, now=NOW + timedelta(seconds=1))
    runner.close()

    resumed = make_runner(tmp_path)
    result = resumed.resume("s")
    assert result["state"] == "CLOSED"
    assert result["records"]["session_reports"][0]["status"] == "CLOSED"
    resumed.close()


def test_incomplete_close_is_explicit_and_idempotent(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    report = runner.close_session("s", now=NOW, complete=False, reason="quote stream ended early")
    assert report["status"] == "INCOMPLETE"
    assert runner.close_session("s", now=NOW, complete=False, reason="different retry detail") == report
    assert runner.state_store.state("s") is SessionState.INCOMPLETE
    runner.close()


def trade_setup(tmp_path, *, risk_allowed=True, include_manifest=True, quote=None, config=None,
                max_quote_age_ms=None, decision_offset_ms=1000, risk_assessment_offset_ms=0):
    runner = make_runner(tmp_path)
    config = config or make_config(max_quote_age_ms=max_quote_age_ms)
    opened(runner, config=config)
    quote = quote or {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
                      "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                                "sell": [{"price": 100.05, "quantity": 3}]}}
    signal_event = normalize_kite_quote("NSE:INFY", quote, received_at=NOW)
    runner.ingest("s", config, [signal_event])
    decided = NOW + timedelta(milliseconds=decision_offset_ms)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(signal_event.event_id,))
    if include_manifest:
        record_manifest(runner, session_id="s", decision_id="trade-1", event=signal_event,
                        decided_at=decided, quantity=11)
    runner.record_risk_verdict(session_id="s", decision_id="trade-1", verdict_id="risk-trade-1",
                               allowed=risk_allowed, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               reason_codes=() if risk_allowed else ("DAILY_LOSS_LIMIT",),
                               assessed_at=decided + timedelta(milliseconds=risk_assessment_offset_ms))
    fill_event = normalize_kite_quote("NSE:INFY", quote, received_at=decided + timedelta(milliseconds=100))
    runner.ingest("s", config, [fill_event], ingest_metadata={
        fill_event.event_id: (fill_event.available_at + timedelta(milliseconds=1), "TEST_FILL_QUOTE_CLOCK")})
    return runner, fill_event, decided


def record_manifest(runner, *, session_id, decision_id, event, decided_at, quantity,
                    cost_model_sha256="6" * 64, day="2026-11-13", holding_sessions=1,
                    selection_lineage=None, persist=True):
    event_sha = runner._con.execute("SELECT event_sha256 FROM runner_events "
                                    "WHERE session_id=? AND event_id=?",
                                    [session_id, event.event_id]).fetchone()[0]
    manifest = {
        "schema": "fb.decision.v1", "day": day, "decision_id": decision_id,
        "author": "test-runner", "strategy_sha256": "1" * 64, "model_sha256": "2" * 64,
        "data_sha256": event_sha, "universe_sha256": "3" * 64, "picks_sha256": "4" * 64,
        "calendar_sha256": "5" * 64, "cost_model_sha256": cost_model_sha256,
        "risk_limits_sha256": "7" * 64, "decision_at_utc": decided_at.isoformat(),
        "entry_earliest_at_utc": (decided_at + timedelta(milliseconds=100)).isoformat(),
        "exit_scheduled_at_utc": (decided_at + timedelta(hours=8)).isoformat(),
        "data_watermark_at_utc": event.available_at.isoformat(),
        "exchange_session_id": f"NSE-{day}", "holding_sessions": holding_sessions,
        "benchmark_id": "NIFTY50", "capital_inr": "100000",
        "predicted_return_fraction": "0.01", "predicted_cost_fraction": "0.001",
        "forecast": {"p10_fraction": "-0.01", "p50_fraction": "0.01",
                     "p90_fraction": "0.02", "probability_exceeds_cost": "0.6"},
        "selections": [{"lineage": selection_lineage or event.instrument_id,
                        "symbol": event.instrument_id,
                        "weight_fraction": "0.1", "quantity": quantity}],
        "exclusions": [], "sources": [{"source_id": event.event_id, "sha256": event_sha,
                                         "published_at_utc": event.event_time.isoformat(),
                                         "received_at_utc": event.received_at.isoformat(),
                                         "available_at_utc": event.available_at.isoformat()}],
        "no_trade_reasons": [],
    }
    if persist:
        return runner.record_decision_manifest(session_id=session_id, decision_id=decision_id, manifest=manifest)
    return manifest


def test_versioned_candidate_runs_through_durable_runner_to_accounting(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    spec = StrategySpec(
        "orb", "v1", {"window": 15}, "test-owner", family="session_breakout",
        code_sha256="a" * 64, asset_scope=("NSE_EQ",), decision_clock="bar_close",
        direction="LONG", stop_exit="range_low", horizon="intraday",
        data_requirements=("ohlcv_1m",),
    )
    eligibility = StrategyEligibility(
        "orb", "v1", "PAPER_ELIGIBLE", "tests/frozen-orb.json", "b" * 64,
        "approved test fixture", spec.hash(),
    )
    config = replace(config, constraints={
        **config.constraints, "strategy_eligibilities": [eligibility.__dict__],
    })
    opened(runner, config=config)
    signal_event = make_event()
    runner.ingest("s", config, [signal_event])
    decided_at = NOW + timedelta(milliseconds=10)
    quote = {
        "timestamp": "2026-11-13 10:15:00", "last_price": 100.05, "volume": 1000,
        "depth": {"buy": [{"price": 100.0, "quantity": 20}],
                  "sell": [{"price": 100.1, "quantity": 3}]},
    }
    fill_event = normalize_kite_quote(
        "NSE:INFY", quote, received_at=decided_at + timedelta(milliseconds=100)
    )
    runner.ingest("s", config, [fill_event])
    signal = CandidateSignal(
        "orb", "v1", "NSE:INFY", "BUY", 2, "candidate-1",
        decided_at.isoformat(), Decimal("100.1"), {"setup": "opening-range"},
    )
    manifest = record_manifest(
        runner, session_id="s", decision_id="candidate-1", event=signal_event,
        decided_at=decided_at, quantity=2, persist=False,
    )
    result = execute_candidate(
        runner,
        session_id="s",
        spec=spec,
        eligibility=eligibility,
        signal=signal,
        input_event_ids=(signal_event.event_id,),
        manifest=manifest,
        risk={"allowed": True, "policy_id": "risk-v1", "policy_sha256": "c" * 64,
              "assessed_at": decided_at},
        execution={
            "event_id": fill_event.event_id,
            "limit_price": Decimal("100.1"),
            "tick_size": Decimal("0.05"),
            "freeze_limit": 1000,
            "slice_size": 1000,
            "adverse_slippage_bps": Decimal("0"),
            "latency_ms": 100,
            "simulated_at": decided_at + timedelta(milliseconds=101),
            "max_wait_ms": 5000,
        },
    )
    records = runner.records("s")
    assert result.receipt is not None
    assert result.receipt["filled_quantity"] == 2
    assert result.receipt["quantity_unit"] == "SHARES"
    assert records["decisions"][0]["payload"]["candidate_intent"]["intent_id"] == result.intent.intent_id
    assert records["intents"][0]["order_id"] == result.intent.intent_id
    assert records["accounting"]["errors"] == []
    assert records["accounting"]["positions"]["NSE:INFY"] == 2
    assert records["accounting"]["simulation_fidelity"] == "OBSERVED_QUOTE_SIM"
    assert [row["state"] for row in records["order_events"]] == ["CREATED", "SUBMITTED", "FILLED"]
    assert records["order_events"][-1]["fill_evidence_id"] is not None
    timing = records["timing_telemetry"]["signals"][0]
    assert timing["orders"][0]["fills"][0]["filled_quantity"] == 2
    assert timing["orders"][0]["simulated_fill_at"] == result.receipt["simulated_at"]
    assert "FEATURE_READY_TIMESTAMP" in timing["missing_stages"]
    with pytest.raises(ForwardSessionRunnerError, match="canonical candidate intent"):
        runner.execute_paper_intent(
            session_id="s", order_id="forged-order", decision_id="candidate-1",
            event_id=fill_event.event_id, side="BUY", quantity=2,
            limit_price=Decimal("100.1"), tick_size=Decimal("0.05"),
            freeze_limit=1000, slice_size=1000, adverse_slippage_bps=Decimal("0"),
            latency_ms=100, quantity_unit="SHARES",
            simulated_at=decided_at + timedelta(milliseconds=101),
            max_wait_ms=5000,
        )
    assert len(runner.records("s")["intents"]) == 1
    runner.close()


def execute(runner, event, decided, *, order_id="order-1", qty=2, limit="100.30",
            execution_offset_ms=100, max_wait_ms=None, price_band=None,
            passive_trade_event_ids=()):
    return runner.execute_paper_intent(
        session_id="s", order_id=order_id, decision_id="trade-1", event_id=event.event_id,
        side="BUY", quantity=qty, limit_price=Decimal(limit), tick_size=Decimal("0.05"),
        freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
        simulated_at=decided + timedelta(milliseconds=execution_offset_ms), max_wait_ms=max_wait_ms,
        price_band=price_band, passive_trade_event_ids=passive_trade_event_ids)


def append_fill_quote(runner, config, signal_event, *, event_id, available_at):
    quote = MarketEvent(event_id, EventKind.QUOTE, signal_event.venue, signal_event.instrument_id,
                        available_at, available_at, available_at, signal_event.payload,
                        signal_event.source, evidence_id=signal_event.evidence_id or "f" * 64)
    runner.ingest("s", config, [quote], ingest_metadata={
        quote.event_id: (available_at + timedelta(milliseconds=1), "TEST_FILL_QUOTE_CLOCK")})
    return quote


def test_durable_outbox_persists_only_causally_eligible_paper_fill_once(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    first = execute(runner, event, decided)
    second = execute(runner, event, decided)
    assert first == second
    assert first["status"] == "FILLED" and first["filled_quantity"] == 2
    assert first["average_price"] == "100.20"
    assert first["physical_order_submitted"] is False
    assert first["effective_instrument_rule"]["effective_from"] == "1900-01-01"
    assert first["effective_instrument_rule"]["tick_size"] == "0.05"
    assert len(first["instrument_rulebook_sha256"]) == 64
    assert runner.records("s")["execution_receipts"] == [first]
    assert runner._con.execute("SELECT COUNT(*) FROM paper_execution_receipts").fetchone()[0] == 1
    accounting = runner.records("s")["accounting"]
    assert accounting["cash_inr"] == "99799.60"
    assert accounting["ledger_cash_inr"] == accounting["cash_inr"]
    assert accounting["positions"] == {"NSE:INFY": 2}
    assert accounting["ledger_balanced"] and accounting["cash_reconciled"]
    assert accounting["charges_assessed"] is False and accounting["net_pnl_eligible"] is False
    runner.close()


def test_paper_order_refuses_quantity_that_violates_effective_instrument_lot(tmp_path):
    config = make_config_with_constraints({
        "instrument_rules": fixture_rule_book("NSE:INFY", lot_size=5),
    })
    runner, event, decided = trade_setup(tmp_path, config=config)
    with pytest.raises(ForwardSessionRunnerError, match="multiple of lot size 5"):
        execute(runner, event, decided, qty=2)
    assert runner.records("s")["intents"] == []
    runner.close()


def test_execution_receipt_records_price_band_rejection_and_late_quote(tmp_path):
    runner, event, decided = trade_setup(tmp_path / "band")
    rejected = execute(runner, event, decided, order_id="band-reject", price_band=(Decimal("90"), Decimal("100")))
    assert rejected["status"] == "REJECTED" and rejected["reject_reason"] == "PRICE_BAND"
    runner.close()

    runner, signal_event, decided = trade_setup(tmp_path / "late")
    delayed = append_fill_quote(runner, make_config(), signal_event, event_id="late-fill-quote",
                                available_at=decided + timedelta(milliseconds=300))
    missed = execute(runner, delayed, decided, order_id="late-order", limit="101", max_wait_ms=100,
                     execution_offset_ms=300)
    assert missed["status"] == "MISSED"
    assert missed["reject_reason"] == "FIRST_QUOTE_AFTER_MAX_WAIT"
    assert runner.records("s")["accounting"]["completed_order_ids"] == ["late-order"]
    runner.close()


def test_passive_paper_receipt_uses_journaled_trade_through_ticks(tmp_path):
    runner, quote, decided = trade_setup(tmp_path)
    tick_at = decided + timedelta(milliseconds=100)
    tick = MarketEvent("passive-trade-through", EventKind.TICK, "NSE", quote.instrument_id,
                       tick_at, tick_at, tick_at, {"price": 99.95, "quantity": 2}, "FIXTURE",
                       evidence_id="e" * 64)
    runner.ingest("s", make_config(), [tick], ingest_metadata={
        tick.event_id: (tick_at + timedelta(milliseconds=1), "FIXTURE_CLOCK")})
    receipt = execute(runner, quote, decided, order_id="passive-order", qty=2, limit="100",
                      passive_trade_event_ids=(tick.event_id,))
    assert receipt["status"] == "FILLED" and receipt["execution_style"] == "PASSIVE_LIMIT"
    assert receipt["average_price"] == "100"
    assert receipt["simulated_adverse_selection_cost_inr"] == "0.20"
    assert receipt["execution_cost_basis"] == "PASSIVE_TRADE_THROUGH_WITH_SEPARATE_ADVERSE_SELECTION"
    assert "charges_inr" not in receipt
    assert receipt["execution_evidence_event_ids"] == [quote.event_id, tick.event_id]
    assert receipt["simulation_fidelity"] == "OBSERVED_TRADE_SIM"
    assert receipt["trade_provenance"][0]["event_sha256"] == tick.sha256()
    assert runner.records("s")["accounting"]["simulation_fidelity"] == "OBSERVED_TRADE_SIM"
    runner.close()


def test_fill_uses_a_new_executable_quote_received_after_the_decision(tmp_path):
    runner, signal_event, decided = trade_setup(tmp_path)
    early_time = decided + timedelta(milliseconds=50)
    early_event = MarketEvent(
        "pre-eligible-fill-quote", EventKind.QUOTE, "NSE", "NSE:INFY", early_time, early_time, early_time,
        {"bid": 100.0, "ask": 100.1, "bid_quantity": 20, "ask_quantity": 20, "volume": 100,
         "depth": {"buy": [[100.0, 20]], "sell": [[100.1, 20]]}},
        "FIXTURE", evidence_id="a" * 64)
    runner.ingest("s", make_config(), [early_event],
                  ingest_metadata={early_event.event_id: (early_time + timedelta(milliseconds=1), "TEST_CLOCK")})
    with pytest.raises(ForwardSessionRunnerError, match="quote predates order eligibility"):
        runner.execute_paper_intent(
            session_id="s", order_id="pre-eligible-fill", decision_id="trade-1",
            event_id=early_event.event_id, side="BUY", quantity=2,
            limit_price=Decimal("100.30"), tick_size=Decimal("0.05"), freeze_limit=10,
            slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
            simulated_at=decided + timedelta(milliseconds=100))
    assert runner.records("s")["intents"] == []
    quote_time = decided + timedelta(milliseconds=150)
    fill_event = MarketEvent(
        "post-decision-fill-quote", EventKind.QUOTE, "NSE", "NSE:INFY", quote_time, quote_time, quote_time,
        {"bid": 100.0, "ask": 100.1, "bid_quantity": 20, "ask_quantity": 20, "volume": 100,
         "depth": {"buy": [[100.0, 20]], "sell": [[100.1, 20]]}},
        "FIXTURE", evidence_id="e" * 64)
    runner.ingest("s", make_config(), [fill_event],
                  ingest_metadata={fill_event.event_id: (quote_time + timedelta(milliseconds=1), "TEST_CLOCK")})
    with pytest.raises(ForwardSessionRunnerError, match="unavailable at simulated execution time"):
        runner.execute_paper_intent(
            session_id="s", order_id="early-fill", decision_id="trade-1", event_id=fill_event.event_id,
            side="BUY", quantity=2, limit_price=Decimal("100.30"), tick_size=Decimal("0.05"),
            freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
            simulated_at=decided + timedelta(milliseconds=120))
    assert runner.records("s")["intents"] == []
    receipt = runner.execute_paper_intent(
        session_id="s", order_id="post-decision-fill", decision_id="trade-1",
        event_id=fill_event.event_id, side="BUY", quantity=2, limit_price=Decimal("100.30"),
        tick_size=Decimal("0.05"), freeze_limit=10, slice_size=10,
        adverse_slippage_bps=Decimal("10"), latency_ms=100,
        simulated_at=decided + timedelta(milliseconds=200))
    assert receipt["status"] == "FILLED"
    assert receipt["quote_event_id"] == fill_event.event_id
    assert receipt["simulation_fidelity"] == "OBSERVED_QUOTE_SIM"
    assert receipt["quote_provenance"]["event_sha256"] == fill_event.sha256()
    assert receipt["quote_provenance"]["exchange"] == "NSE"
    assert receipt["quote_provenance"]["available_at_utc"] == fill_event.available_at.isoformat()
    decision_inputs = runner.records("s")["decisions"][0]["input_event_ids"]
    assert decision_inputs
    assert signal_event.event_id not in decision_inputs
    assert fill_event.event_id not in runner.records("s")["decisions"][0]["input_event_ids"]
    runner.close()


def test_one_tick_worse_post_decision_quote_worsens_simulated_buy_fill(tmp_path):
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
             "depth": {"buy": [{"price": 100.0, "quantity": 20}],
                       "sell": [{"price": 100.1, "quantity": 20}]}}
    baseline_runner, baseline_event, baseline_decided = trade_setup(tmp_path / "baseline", quote=quote)
    baseline = execute(baseline_runner, baseline_event, baseline_decided)
    baseline_runner.close()

    worse_quote = {**quote, "depth": {"buy": quote["depth"]["buy"],
                                       "sell": [{"price": 100.15, "quantity": 20}]}}
    worse_runner, worse_event, worse_decided = trade_setup(tmp_path / "worse", quote=worse_quote)
    worse = execute(worse_runner, worse_event, worse_decided)
    worse_runner.close()
    assert Decimal(worse["average_price"]) == Decimal(baseline["average_price"]) + Decimal("0.05")


def test_ltp_only_trade_decision_is_recordable_but_never_produces_paper_fill(tmp_path):
    runner, event, decided = trade_setup(
        tmp_path,
        quote={"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000},
    )
    assert event.kind is EventKind.TICK
    assert runner.records("s")["decisions"][0]["outcome"] == "TRADE"
    with pytest.raises(ForwardSessionRunnerError, match="two-sided quote with positive visible depth"):
        execute(runner, event, decided)
    assert runner.records("s")["intents"] == []
    assert runner.records("s")["execution_receipts"] == []
    runner.close()


def test_stale_quote_is_refused_at_decision_time_by_the_frozen_age_limit(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config(max_quote_age_ms=500)
    opened(runner, config=config)
    event = make_event()
    runner.ingest("s", config, [event])
    with pytest.raises(ForwardSessionRunnerError, match="decision quote exceeds configured maximum age of 500ms"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="stale-trade", outcome="TRADE",
                      input_event_ids=(event.event_id,), decided_at=NOW + timedelta(seconds=1))
    assert runner.records("s")["decisions"] == []
    runner.close()


def test_old_exchange_quote_received_just_now_is_refused_at_decision(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config(max_quote_age_ms=500)
    opened(runner, config=config)
    event = make_event(event_time=NOW - timedelta(seconds=2), available_at=NOW)
    runner.ingest("s", config, [event])
    with pytest.raises(ForwardSessionRunnerError, match="decision quote exceeds configured maximum age of 500ms"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="delayed-stale-trade",
                      outcome="TRADE", input_event_ids=(event.event_id,),
                      decided_at=NOW + timedelta(milliseconds=100))
    assert runner.records("s")["decisions"] == []
    runner.close()


def test_quote_that_ages_out_during_simulated_latency_cannot_create_an_intent(tmp_path):
    runner, event, decided = trade_setup(tmp_path, max_quote_age_ms=150, decision_offset_ms=100)
    with pytest.raises(ForwardSessionRunnerError, match="paper fill quote exceeds configured maximum age of 150ms"):
        execute(runner, event, decided, execution_offset_ms=500)
    assert runner.records("s")["intents"] == []
    assert runner.records("s")["execution_receipts"] == []
    runner.close()


def test_forward_paper_intent_cannot_reach_broker_with_forged_live_flags(tmp_path, monkeypatch):
    import urllib.request

    from financial_brain.execution.oms.kite_broker import KiteBroker
    from financial_brain.forward_edge import modes

    calls = []
    monkeypatch.setattr(KiteBroker, "place", lambda *_args, **_kwargs: calls.append("place"))
    monkeypatch.setattr(KiteBroker, "_request",
                        lambda *_args, **_kwargs: pytest.fail("paper session called Kite transport"))
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_args, **_kwargs: pytest.fail("paper session opened an HTTP transport"))
    monkeypatch.setattr(modes, "PHYSICAL_ORDERS_ENABLED", True)
    monkeypatch.setenv("FB_LIVE_ORDERS", "1")
    runner, event, decided = trade_setup(tmp_path)
    receipt = execute(runner, event, decided)
    assert receipt["status"] == "FILLED"
    assert receipt["physical_order_submitted"] is False
    assert calls == []
    runner.close()


def test_paper_accounting_replays_sell_inventory_and_gross_realized_pnl(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    execute(runner, event, decided)
    before = runner.records("s")["accounting"]
    assert before["positions"] == {"NSE:INFY": 2}

    sell_decision_at = decided + timedelta(seconds=1)
    config = make_config()
    runner.decide(session_id="s", strategy_id="momentum", decision_id="sell-1", outcome="TRADE",
                  decided_at=sell_decision_at, input_event_ids=(event.event_id,))
    record_manifest(runner, session_id="s", decision_id="sell-1", event=event,
                    decided_at=sell_decision_at, quantity=2)
    runner.record_risk_verdict(session_id="s", decision_id="sell-1", verdict_id="risk-sell-1",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=sell_decision_at)
    sell_event = append_fill_quote(runner, config, event, event_id="sell-quote",
                                   available_at=sell_decision_at + timedelta(milliseconds=100))
    sold = runner.execute_paper_intent(
        session_id="s", order_id="sell-order", decision_id="sell-1", event_id=sell_event.event_id,
        side="SELL", quantity=2, limit_price=Decimal("99.00"), tick_size=Decimal("0.05"),
        freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
        simulated_at=sell_decision_at + timedelta(milliseconds=100))
    assert sold["status"] == "FILLED"
    after = runner.records("s")["accounting"]
    assert after["positions"] == {}
    assert after["realized_gross_pnl_inr"] == "-0.70"
    assert after["cash_inr"] == after["ledger_cash_inr"] == "99999.30"
    assert after["ledger_balanced"] and after["cash_reconciled"]
    runner.close()


def test_forward_cost_schedule_reduces_realized_pnl_and_preserves_negative_net(tmp_path):
    from financial_brain.costs.india import CostModel
    from financial_brain.costs.india import Segment
    from financial_brain.forward_edge.cost_schedule import CostSchedule

    constraints = {
        "liquidity_bucket": "large",
        "cost_schedule": {
            "schedule_id": "test-india", "version": "v1", "effective_from": "2026-01-01",
            "effective_to": None, "segment": "delivery", "model": CostModel().as_dict(),
            "source_ref": "test tariff fixture", "source_sha256": "c" * 64,
        },
    }
    runner = make_runner(tmp_path)
    configured = opened(runner, config=make_config_with_constraints(constraints))
    assert configured is SessionState.OPEN
    event = MarketEvent("cost-quote", EventKind.QUOTE, "NSE", "NSE:INFY", NOW, NOW, NOW,
                        {"bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 10,
                         "volume": 100, "depth": {"buy": [[100.0, 10]], "sell": [[100.1, 10]]}},
                        "FIXTURE", evidence_id="d" * 64)
    runner.ingest("s", make_config_with_constraints(constraints), [event])
    schedule = CostSchedule("test-india", "v1", date(2026, 1, 1), None, Segment.DELIVERY,
                            CostModel(), "test tariff fixture", "c" * 64)
    decided = NOW + timedelta(milliseconds=100)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(event.event_id,))
    record_manifest(runner, session_id="s", decision_id="trade-1", event=event,
                    decided_at=decided, quantity=11, cost_model_sha256=schedule.digest())
    runner.record_risk_verdict(session_id="s", decision_id="trade-1", verdict_id="risk-trade-1",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=decided)
    fill_event = append_fill_quote(runner, make_config_with_constraints(constraints), event,
                                   event_id="cost-fill-quote",
                                   available_at=decided + timedelta(milliseconds=100))
    buy = runner.execute_paper_intent(
        session_id="s", order_id="buy", decision_id="trade-1", event_id=fill_event.event_id,
        side="BUY", quantity=2, limit_price=Decimal("100.30"), tick_size=Decimal("0.05"),
        freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
        simulated_at=decided + timedelta(milliseconds=100))
    assert buy["charges_inr"] > "0"
    assert buy["cost_fidelity"]["schema"] == "fb.cost_fidelity.v1"
    assert buy["cost_fidelity"]["quote_source"] == "FIXTURE"
    assert buy["cost_fidelity"]["provider_clock"] == fill_event.event_time.isoformat()
    assert buy["cost_fidelity"]["cost_model_version"] == "v1"
    assert buy["cost_fidelity"]["cost_model_id"] == "test-india"
    assert buy["cost_fidelity"]["cost_schedule_sha256"] == "c" * 64
    assert buy["cost_fidelity"]["impact_calibration"] == "UNAVAILABLE"
    assert "impact_calibration" in buy["cost_fidelity"]["unavailable_fields"]
    assert buy["execution_cost_basis"] == "OBSERVED_DEPTH_WITH_SLIPPAGE_EMBEDDED_IN_FILL_PRICE"
    assert buy["simulated_adverse_selection_cost_inr"] == "0.00"
    assert buy["cost_items"][0]["impact"] == 0
    assert all(item.get("type") != "SIMULATED_ADVERSE_SELECTION" for item in buy["cost_items"])
    sell = runner.execute_paper_intent(
        session_id="s", order_id="sell", decision_id="trade-1", event_id=fill_event.event_id,
        side="SELL", quantity=2, limit_price=Decimal("99.00"), tick_size=Decimal("0.05"),
        freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
        simulated_at=decided + timedelta(milliseconds=100))
    accounting = runner.records("s")["accounting"]
    assert sell["charges_inr"] > "0"
    assert accounting["realized_gross_pnl_inr"] == "-0.70"
    assert Decimal(accounting["realized_net_pnl_inr"]) < Decimal(accounting["realized_gross_pnl_inr"])
    assert Decimal(accounting["realized_net_pnl_inr"]) < 0
    assert accounting["net_pnl_eligible"] is True
    assert accounting["status"] == "RECONCILED_NET"
    assert accounting["cash_inr"] == accounting["ledger_cash_inr"]
    assert Decimal(accounting["cash_inr"]) < Decimal("100000") - Decimal("0.70")
    assert all(item["cost_items"][0]["schedule_sha256"] for item in accounting["cost_line_items"])
    runner.close()


def test_forward_mixed_costed_and_uncosted_fills_are_not_net_eligible(tmp_path):
    from financial_brain.costs.india import CostModel, Segment
    from financial_brain.forward_edge.cost_schedule import CostSchedule

    constraints = {"max_positions": 1, "liquidity_bucket": "large", "cost_schedule": {
        "schedule_id": "test-india", "version": "v1", "effective_from": "2026-01-01",
        "effective_to": None, "segment": "delivery", "model": CostModel().as_dict(),
        "source_ref": "test tariff fixture", "source_sha256": "c" * 64}}
    runner = make_runner(tmp_path)
    config = make_config_with_constraints(constraints)
    opened(runner, config=config)
    event = MarketEvent("cost-quote", EventKind.QUOTE, "NSE", "NSE:INFY", NOW, NOW, NOW,
                        {"bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 10,
                         "volume": 100, "depth": {"buy": [[100.0, 10]], "sell": [[100.1, 10]]}},
                        "FIXTURE", evidence_id="d" * 64)
    runner.ingest("s", config, [event])
    schedule = CostSchedule("test-india", "v1", date(2026, 1, 1), None, Segment.DELIVERY,
                            CostModel(), "test tariff fixture", "c" * 64)
    decided = NOW + timedelta(milliseconds=100)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(event.event_id,))
    record_manifest(runner, session_id="s", decision_id="trade-1", event=event,
                    decided_at=decided, quantity=11, cost_model_sha256=schedule.digest())
    runner.record_risk_verdict(session_id="s", decision_id="trade-1", verdict_id="risk-trade-1",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=decided)
    fill_event = append_fill_quote(runner, config, event, event_id="legacy-fill-quote",
                                   available_at=decided + timedelta(milliseconds=100))
    execute(runner, fill_event, decided)
    # A legacy persisted receipt without cost evidence must remain gross-only.
    row = runner._con.execute("SELECT receipt_json FROM paper_execution_receipts WHERE order_id='order-1'").fetchone()
    receipt = __import__("json").loads(row[0])
    receipt.pop("cost_items")
    receipt.pop("charges_inr")
    from financial_brain.forward_edge.paper_accounting import rebuild_paper_accounting
    records = runner.records("s")
    legacy = rebuild_paper_accounting(account_id="ledger-1", capital=Decimal("100000"),
                                      events=records["events"], intents=records["intents"], receipts=[receipt])
    assert legacy["status"] == "RECONCILED_GROSS"
    assert legacy["net_pnl_eligible"] is False
    assert legacy["realized_net_pnl_inr"] is None
    runner.close()


def test_legacy_file_outbox_states_migrate_into_the_runner_journal(tmp_path):
    from financial_brain.kernel.events import EventKernel, UnknownOutcome

    legacy_path = tmp_path / "journal-outbox.sqlite"
    legacy = EventKernel(journal_path=str(legacy_path))
    with pytest.raises(UnknownOutcome):
        legacy.side_effect("paper:legacy:unknown", lambda: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(UnknownOutcome):
        legacy.side_effect("paper:legacy:retry", lambda: (_ for _ in ()).throw(RuntimeError("crash")))
    legacy.resolve_unknown("paper:legacy:retry", took_effect=False, reconciled_by="test-operator")
    legacy.close()

    runner = make_runner(tmp_path)
    states = dict(runner._con.execute("SELECT key, state FROM outbox ORDER BY key"))
    assert states == {"paper:legacy:retry": "PENDING", "paper:legacy:unknown": "UNKNOWN"}
    reconciliation = runner._con.execute(
        "SELECT took_effect, reconciled_by FROM reconciliations WHERE key='paper:legacy:retry'"
    ).fetchone()
    assert reconciliation == (0, "test-operator")
    runner.close()


def test_paper_sell_is_rejected_when_long_inventory_is_insufficient(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    with pytest.raises(ForwardSessionRunnerError, match="exceeds reconciled long inventory"):
        runner.execute_paper_intent(
            session_id="s", order_id="short-attempt", decision_id="trade-1", event_id=event.event_id,
            side="SELL", quantity=1, limit_price=Decimal("99.00"), tick_size=Decimal("0.05"),
            freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
            simulated_at=decided + timedelta(milliseconds=100))
    assert runner.records("s")["intents"] == []
    runner.close()


def test_paper_intent_requires_an_explicit_allowed_risk_verdict(tmp_path):
    runner, event, decided = trade_setup(tmp_path, risk_allowed=False)
    with pytest.raises(ForwardSessionRunnerError, match="explicit persisted allow risk verdict"):
        execute(runner, event, decided)
    assert runner.records("s")["intents"] == []
    runner.close()


def test_paper_fill_cannot_precede_risk_assessment_clock(tmp_path):
    runner, event, decided = trade_setup(tmp_path, risk_assessment_offset_ms=200)
    with pytest.raises(ForwardSessionRunnerError, match="cannot precede the persisted risk assessment"):
        execute(runner, event, decided)
    assert runner.records("s")["intents"] == []
    runner.close()


def test_paper_intent_requires_canonical_manifest_and_respects_its_quantity(tmp_path):
    runner, event, decided = trade_setup(tmp_path, include_manifest=False)
    with pytest.raises(ForwardSessionRunnerError, match="canonical persisted decision manifest"):
        execute(runner, event, decided)
    runner.close()

    runner, event, decided = trade_setup(tmp_path / "oversize", include_manifest=True)
    with pytest.raises(ForwardSessionRunnerError, match="exceeds the manifest's selected instrument quantity"):
        execute(runner, event, decided, qty=12)
    runner.close()


def test_append_only_audit_rebuilds_daily_views_and_records_data_rejection(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    execute(runner, event, decided)
    baseline = runner.records("s")
    assert [item["kind"] for item in baseline["audit_log"]] == [
        "preflight", "market_event", "decision", "decision_manifest", "risk_verdict", "market_event",
        "paper_intent", "execution_receipt"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        runner._con.execute("UPDATE runner_audit_log SET record_json='{}' WHERE session_id='s'")
    runner.rebuild_materialized_views("s")
    assert runner.records("s") == baseline

    outside = normalize_kite_quote("NSE:OTHER", {
        "timestamp": "2026-11-13 10:15:01", "last_price": 10.0, "volume": 10,
    }, received_at=NOW)
    with pytest.raises(ForwardSessionRunnerError, match="outside frozen universe"):
        runner.ingest("s", make_config(), [outside])
    rejected = runner.records("s")["data_rejections"]
    assert len(rejected) == 1 and rejected[0]["source_event_ids"] == [outside.event_id]
    assert "outside frozen universe" in rejected[0]["reason"]
    runner.close_session("s", complete=False, reason="test evidence close")
    closed_records = runner.records("s")
    runner.rebuild_materialized_views("s")
    assert runner.records("s") == closed_records
    runner.close()


def test_trading_audit_rows_bind_source_commit_and_executable_runner(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    execute(runner, event, decided)
    rows = runner.records("s")["audit_log"]
    for kind in ("market_event", "decision", "decision_manifest", "risk_verdict",
                 "paper_intent", "execution_receipt"):
        row = next(item for item in rows if item["kind"] == kind)
        provenance = row["record"]["provenance"]
        assert provenance["executable_entry_point"] == "ForwardSessionRunner"
        assert provenance["source_tree_state"] in {"CLEAN", "DIRTY", "UNKNOWN"}
        assert "source_commit" in provenance
        assert "branch_ancestry_sha256" in provenance
    runner.close()


def test_lifecycle_event_and_audit_append_roll_back_and_resume_atomically(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    record = {"session_id": "s", "occurred_at": NOW.isoformat(), "status": "OPEN"}
    append_audit = runner.durable.append_audit

    def fail_audit(**kwargs):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(runner.durable, "append_audit", fail_audit)
    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        runner._append_record("preflight", "s", record)
    assert runner._con.execute(
        "SELECT COUNT(*) FROM runner_events WHERE session_id='s'").fetchone()[0] == 0
    assert runner._con.execute(
        "SELECT COUNT(*) FROM runner_audit_log WHERE session_id='s'").fetchone()[0] == 0

    monkeypatch.setattr(runner.durable, "append_audit", append_audit)
    runner._append_record("preflight", "s", record)
    expected = runner.durable.audit_records("s")
    assert len(expected) == 1
    runner.close()

    resumed = make_runner(tmp_path)
    assert resumed.durable.audit_records("s") == expected
    assert resumed._append_record("preflight", "s", record) is None
    assert resumed.durable.audit_records("s") == expected
    resumed.close()


def test_shared_paper_ledger_carries_inventory_across_sessions_without_expiry(tmp_path):
    runner, buy_quote, buy_decision_at = trade_setup(tmp_path)
    buy_receipt = execute(runner, buy_quote, buy_decision_at, order_id="same-local-order-id", qty=2)
    first_close_at = buy_decision_at + timedelta(seconds=2)
    runner.close_session("s", complete=True, now=first_close_at)
    first_day_accounting = runner.records("s")["accounting"]
    assert first_day_accounting["positions"] == {"NSE:INFY": 2}
    assert buy_receipt["filled_quantity"] == 2

    second_day = date(2026, 11, 16)
    second_config = replace(make_config(), exchange_date=second_day)
    second_start = NOW + timedelta(days=3)
    assert runner.begin("s2", second_config, now=second_start,
                        preflight={**READY_PREFLIGHT, "risk_policy_loaded": True}) is SessionState.OPEN
    before_sell = runner.records("s2")["accounting"]
    assert before_sell["positions"] == {"NSE:INFY": 2}
    assert before_sell["cash_inr"] == first_day_accounting["cash_inr"]

    signal = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-16 10:15:00", "last_price": 101.0, "volume": 1000,
        "depth": {"buy": [{"price": 100.95, "quantity": 20}],
                  "sell": [{"price": 101.05, "quantity": 20}]},
    }, received_at=second_start)
    runner.ingest("s2", second_config, [signal])
    decision_at = second_start + timedelta(seconds=1)
    runner.decide(session_id="s2", strategy_id="momentum", decision_id="sell-trade",
                  outcome="TRADE", input_event_ids=(signal.event_id,), decided_at=decision_at)
    record_manifest(runner, session_id="s2", decision_id="sell-trade", event=signal,
                    decided_at=decision_at, quantity=2, day=second_day.isoformat(),
                    holding_sessions=20)
    runner.record_risk_verdict(session_id="s2", decision_id="sell-trade", verdict_id="sell-risk",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               reason_codes=(), assessed_at=decision_at)
    fill_at = decision_at + timedelta(milliseconds=100)
    fill_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-16 10:15:01", "last_price": 101.0, "volume": 1000,
        "depth": {"buy": [{"price": 100.95, "quantity": 20}],
                  "sell": [{"price": 101.05, "quantity": 20}]},
    }, received_at=fill_at)
    runner.ingest("s2", second_config, [fill_quote], ingest_metadata={
        fill_quote.event_id: (fill_at + timedelta(milliseconds=1), "TEST_FILL_QUOTE_CLOCK")})
    sell_receipt = runner.execute_paper_intent(
        session_id="s2", order_id="same-local-order-id", decision_id="sell-trade",
        event_id=fill_quote.event_id, side="SELL", quantity=2, limit_price=Decimal("100.90"),
        tick_size=Decimal("0.05"), freeze_limit=10, slice_size=10,
        adverse_slippage_bps=Decimal("0"), latency_ms=100,
        simulated_at=fill_at + timedelta(milliseconds=50))
    assert sell_receipt["filled_quantity"] == 2
    second_close_at = fill_at + timedelta(seconds=1)
    second_day_accounting = runner.records("s2")["accounting"]
    assert second_day_accounting["positions"] == {}
    assert second_day_accounting["filled_order_count"] == 2
    runner.close_session("s2", complete=True, now=second_close_at)

    # A later session must not mutate an earlier session's frozen account projection.
    assert runner.records("s")["accounting"] == first_day_accounting
    runner.close()


def test_paper_book_adapter_records_qualified_horizon_and_abstains_on_uncalibrated_factor(
        tmp_path):
    exchange_day = date(2026, 11, 16)
    start = NOW + timedelta(days=3)
    config = replace(make_config(), exchange_date=exchange_day)
    runner = make_runner(tmp_path)
    assert runner.begin("book-adapter", config, now=start,
                        preflight={**READY_PREFLIGHT, "risk_policy_loaded": True}) is SessionState.OPEN
    quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-16 10:15:00", "last_price": 101.0, "volume": 1000,
        "depth": {"buy": [{"price": 100.95, "quantity": 20}],
                  "sell": [{"price": 101.05, "quantity": 20}]},
    }, received_at=start)
    runner.ingest("book-adapter", config, [quote])
    book = duckdb.connect(":memory:")
    book.execute("""CREATE TABLE positions (
        version VARCHAR, mode VARCHAR, signal_day DATE, rank INTEGER, ticker VARCHAR, lineage VARCHAR,
        type VARCHAR, horizon INTEGER, shares INTEGER, stop DOUBLE, ref_price DOUBLE,
        expected_net DOUBLE, cost DOUBLE, entry_day DATE, entry_px DOUBLE,
        exit_day DATE, exit_px DOUBLE, exit_reason VARCHAR, last_day DATE, last_px DOUBLE,
        held INTEGER, bench DOUBLE, why VARCHAR)""")
    book.execute("INSERT INTO positions (version, mode, signal_day, rank, ticker, lineage, type, horizon, shares, "
                 "stop, ref_price, expected_net, cost, entry_day, entry_px, exit_day, exit_px) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ["swing-v1", "live", date(2026, 11, 13), 1, "INFY", "INFY-LINEAGE", "swing",
                  7, 2, 95.0, 100.0, 0.02, 0.003, None, None, None, None])
    signal, = pending_signals(book, mode="live", version="swing-v1", exchange_date=exchange_day,
                              instrument_ids={"INFY": "NSE:INFY"})
    decision_at = start + timedelta(seconds=1)
    decision_id = signal.signal_id + ":book-adapter"
    manifest = record_manifest(runner, session_id="book-adapter", decision_id=decision_id,
                               event=quote, decided_at=decision_at, quantity=signal.shares,
                               day=exchange_day.isoformat(), holding_sessions=signal.horizon_sessions,
                               selection_lineage=signal.lineage, persist=False)
    assert record_signal_decision(
        runner=runner, session_id="book-adapter", signal=signal, quote_event_id=quote.event_id,
        decided_at=decision_at, manifest=manifest) == decision_id
    trade = runner.records("book-adapter")["decisions"][0]
    assert trade["outcome"] == "TRADE"
    assert trade["payload"]["horizon_sessions"] == 7
    assert runner.records("book-adapter")["intents"] == []
    with pytest.raises(ForwardSessionRunnerError, match="explicit persisted allow risk verdict"):
        runner.execute_paper_intent(
            session_id="book-adapter", order_id="unapproved-book-order", decision_id=decision_id,
            event_id=quote.event_id, side="BUY", quantity=signal.shares,
            limit_price=Decimal("101.10"), tick_size=Decimal("0.05"), freeze_limit=10,
            slice_size=10, adverse_slippage_bps=Decimal("0"), latency_ms=0,
            simulated_at=decision_at + timedelta(seconds=1))
    assert runner.records("book-adapter")["intents"] == []
    runner.record_risk_verdict(session_id="book-adapter", decision_id=decision_id,
                               verdict_id="book-signal-risk", allowed=True, policy_id="test-risk-v1",
                               policy_sha256="a" * 64, assessed_at=decision_at)
    fill_at = decision_at + timedelta(milliseconds=100)
    fill_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-16 10:15:01", "last_price": 101.0, "volume": 1000,
        "depth": {"buy": [{"price": 100.95, "quantity": 20}],
                  "sell": [{"price": 101.05, "quantity": 20}]},
    }, received_at=fill_at)
    runner.ingest("book-adapter", config, [fill_quote], ingest_metadata={
        fill_quote.event_id: (fill_at + timedelta(milliseconds=1), "TEST_FILL_QUOTE_CLOCK")})
    receipt = runner.execute_paper_intent(
        session_id="book-adapter", order_id="book-order", decision_id=decision_id,
        event_id=fill_quote.event_id, side="BUY", quantity=WholeUnits(Decimal(signal.shares)),
        limit_price=Price(Decimal("101.10"), "INR"),
        tick_size=Price(Decimal("0.05"), "INR"), freeze_limit=10,
        slice_size=10, adverse_slippage_bps=BasisPoints(Decimal("0")), latency_ms=100,
        simulated_at=fill_at + timedelta(milliseconds=50))
    assert receipt["filled_quantity"] == signal.shares
    assert runner.records("book-adapter")["accounting"]["positions"] == {"NSE:INFY": signal.shares}
    assert reconcile_entry_receipt(book=book, runner=runner, session_id="book-adapter", signal=signal,
                                   decision_id=decision_id, order_id="book-order", receipt=receipt) == "FILLED"
    assert reconcile_entry_receipt(book=book, runner=runner, session_id="book-adapter", signal=signal,
                                   decision_id=decision_id, order_id="book-order", receipt=receipt) == "FILLED"
    position = book.execute("SELECT entry_day, entry_px, shares, held FROM positions "
                            "WHERE version='swing-v1'").fetchone()
    assert position == (exchange_day, float(receipt["average_price"]), signal.shares, 0)
    assert pending_signals(book, mode="live", version="swing-v1", exchange_date=exchange_day + timedelta(days=1),
                          instrument_ids={"INFY": "NSE:INFY"}) == ()

    book.execute("DELETE FROM positions")
    book.execute("INSERT INTO positions (version, mode, signal_day, rank, ticker, lineage, type, horizon, shares, "
                 "stop, ref_price, expected_net, cost, entry_day, entry_px, exit_day, exit_px) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ["momentum-v1", "live", date(2026, 11, 13), 1, "INFY", "INFY-LINEAGE", "momentum",
                  20, 2, 95.0, 100.0, 0.0, 0.003, None, None, None, None])
    uncalibrated, = pending_signals(book, mode="live", version="momentum-v1", exchange_date=exchange_day,
                                    instrument_ids={"INFY": "NSE:INFY"})
    abstention_id = record_signal_decision(
        runner=runner, session_id="book-adapter", signal=uncalibrated, quote_event_id=quote.event_id,
        decided_at=decision_at + timedelta(milliseconds=1))
    abstention = next(item for item in runner.records("book-adapter")["decisions"]
                      if item["decision_id"] == abstention_id)
    assert abstention["outcome"] == "NO_TRADE"
    book.close()
    runner.close_session("book-adapter", complete=False, now=decision_at + timedelta(seconds=1),
                         reason="adapter contract test")
    runner.close()


def test_forward_paper_book_signal_accepts_typed_prices_units_and_rate_bps():
    signal = PaperBookSignal(
        version="swing-v1", mode="live", signal_day=date(2026, 11, 13), rank=1,
        ticker="INFY", lineage="INFY-LINEAGE", signal_type="swing",
        horizon_sessions=7, shares=WholeUnits(Decimal("2")),
        stop_price=Price(Decimal("95"), "INR"),
        reference_price=Price(Decimal("100"), "INR"),
        expected_net=BasisPoints(Decimal("200")),
        estimated_cost=BasisPoints(Decimal("30")), instrument_id="NSE:INFY",
    )
    assert signal.shares == 2
    assert signal.stop_price == 95.0
    assert signal.reference_price == 100.0
    assert signal.expected_net == 0.02
    assert signal.estimated_cost == 0.003


@pytest.mark.parametrize("with_cost_schedule", [False, True])
@pytest.mark.parametrize(("version", "horizon"), [
    ("book-v1", 20), ("swing-v1", 7), ("momentum-v1", 20),
])
def test_paper_book_stop_exit_uses_runner_quote_and_reconciles_sell(
        tmp_path, with_cost_schedule, version, horizon):
    runner = make_runner(tmp_path)
    if with_cost_schedule:
        from financial_brain.costs.india import CostModel, Segment
        from financial_brain.forward_edge.cost_schedule import CostSchedule

        constraints = {"liquidity_bucket": "large", "cost_schedule": {
            "schedule_id": "test-india", "version": "v1", "effective_from": "2026-01-01",
            "effective_to": None, "segment": "delivery", "model": CostModel().as_dict(),
            "source_ref": "test tariff fixture", "source_sha256": "c" * 64}}
        schedule = CostSchedule("test-india", "v1", date(2026, 1, 1), None, Segment.DELIVERY,
                                CostModel(), "test tariff fixture", "c" * 64)
        cost_digest = schedule.digest()
        entry_config = make_config_with_constraints(constraints)
    else:
        cost_digest = "6" * 64
        entry_config = make_config()
    opened(runner, "s", entry_config)
    signal_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
        "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                  "sell": [{"price": 100.05, "quantity": 20}]},
    }, received_at=NOW)
    runner.ingest("s", entry_config, [signal_quote])
    decision_at = NOW + timedelta(seconds=1)
    book = duckdb.connect(":memory:")
    book.execute("""CREATE TABLE positions (
        version VARCHAR, mode VARCHAR, signal_day DATE, rank INTEGER, ticker VARCHAR, lineage VARCHAR,
        type VARCHAR, horizon INTEGER, shares INTEGER, stop DOUBLE, ref_price DOUBLE,
        expected_net DOUBLE, cost DOUBLE, entry_day DATE, entry_px DOUBLE,
        exit_day DATE, exit_px DOUBLE, exit_reason VARCHAR, last_day DATE, last_px DOUBLE,
        held INTEGER, bench DOUBLE, why VARCHAR)""")
    book.execute("INSERT INTO positions (version, mode, signal_day, rank, ticker, lineage, type, horizon, shares, "
                 "stop, ref_price, expected_net, cost, entry_day, entry_px, exit_day, exit_px) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 [version, "live", date(2026, 11, 12), 1, "INFY", "INFY-LINEAGE", version,
                  horizon, 2, 95.0, 100.0, 0.02, 0.003, None, None, None, None])
    signal, = pending_signals(book, mode="live", version=version, exchange_date=date(2026, 11, 13),
                              instrument_ids={"INFY": "NSE:INFY"})
    entry_decision_id = signal.signal_id + ":s"
    entry_manifest = record_manifest(runner, session_id="s", decision_id=entry_decision_id,
                                     event=signal_quote, decided_at=decision_at, quantity=signal.shares,
                                     cost_model_sha256=cost_digest, selection_lineage=signal.lineage,
                                     holding_sessions=signal.horizon_sessions, persist=False)
    record_signal_decision(runner=runner, session_id="s", signal=signal,
                           quote_event_id=signal_quote.event_id, decided_at=decision_at,
                           manifest=entry_manifest)
    runner.record_risk_verdict(session_id="s", decision_id=entry_decision_id, verdict_id="entry-risk",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=decision_at)
    entry_fill_at = decision_at + timedelta(milliseconds=100)
    entry_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:01", "last_price": 100.0, "volume": 1000,
        "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                  "sell": [{"price": 100.05, "quantity": 20}]},
    }, received_at=entry_fill_at)
    runner.ingest("s", entry_config, [entry_quote], ingest_metadata={
        entry_quote.event_id: (entry_fill_at + timedelta(milliseconds=1), "TEST_FILL_QUOTE_CLOCK")})
    entry_receipt = runner.execute_paper_intent(
        session_id="s", order_id="book-entry", decision_id=entry_decision_id,
        event_id=entry_quote.event_id, side="BUY", quantity=signal.shares,
        limit_price=Decimal("100.10"), tick_size=Decimal("0.05"), freeze_limit=10,
        slice_size=10, adverse_slippage_bps=Decimal("0"), latency_ms=100,
        simulated_at=entry_fill_at + timedelta(milliseconds=50))
    assert reconcile_entry_receipt(book=book, runner=runner, session_id="s", signal=signal,
                                   decision_id=entry_decision_id, order_id="book-entry",
                                   receipt=entry_receipt) == "FILLED"
    assert due_exits(book, runner=runner, session_id="s", mode="live", version=version,
                     exchange_date=date(2026, 11, 13), completed_sessions_held={signal.signal_id: 0},
                     instrument_ids={"INFY": "NSE:INFY"},
                     quote_events={"NSE:INFY": entry_quote}) == ()
    runner.close_session("s", complete=True, now=entry_fill_at + timedelta(seconds=2))

    exit_day = date(2026, 11, 16)
    exit_config = replace(entry_config, exchange_date=exit_day)
    exit_start = NOW + timedelta(days=3)
    runner.begin("s2", exit_config, now=exit_start, preflight={**READY_PREFLIGHT, "risk_policy_loaded": True})
    stop_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-16 10:15:00", "last_price": 94.9, "volume": 1000,
        "depth": {"buy": [{"price": 94.95, "quantity": 20}],
                  "sell": [{"price": 95.05, "quantity": 20}]},
    }, received_at=exit_start)
    runner.ingest("s2", exit_config, [stop_quote])
    exits = due_exits(book, runner=runner, session_id="s2", mode="live", version=version,
                      exchange_date=exit_day, completed_sessions_held={signal.signal_id: 1},
                      instrument_ids={"INFY": "NSE:INFY"}, quote_events={"NSE:INFY": stop_quote})
    assert len(exits) == 1 and exits[0].reason == "STOP"
    assert exits[0].completed_sessions_held == 1
    book.execute("UPDATE positions SET stop=90 WHERE version=?", [version])
    time_exits = due_exits(book, runner=runner, session_id="s2", mode="live", version=version,
                           exchange_date=exit_day,
                           completed_sessions_held={signal.signal_id: signal.horizon_sessions},
                           instrument_ids={"INFY": "NSE:INFY"},
                           quote_events={"NSE:INFY": stop_quote})
    assert len(time_exits) == 1 and time_exits[0].reason == "TIME"
    book.execute("UPDATE positions SET stop=95 WHERE version=?", [version])
    exit_decision_at = exit_start + timedelta(seconds=1)
    exit_decision_id = f"paper-book-exit:{signal.signal_id}:s2"
    exit_manifest = record_manifest(runner, session_id="s2", decision_id=exit_decision_id,
                                    event=stop_quote, decided_at=exit_decision_at,
                                    quantity=signal.shares, day=exit_day.isoformat(),
                                    cost_model_sha256=cost_digest,
                                    holding_sessions=signal.horizon_sessions,
                                    selection_lineage=signal.lineage, persist=False)
    record_exit_decision(runner=runner, session_id="s2", exit_signal=exits[0],
                         decided_at=exit_decision_at, manifest=exit_manifest)
    runner.record_risk_verdict(session_id="s2", decision_id=exit_decision_id, verdict_id="exit-risk",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=exit_decision_at)
    exit_fill_at = exit_decision_at + timedelta(milliseconds=100)
    exit_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-16 10:15:01", "last_price": 94.0, "volume": 1000,
        "depth": {"buy": [{"price": 94.05, "quantity": 20}],
                  "sell": [{"price": 94.15, "quantity": 20}]},
    }, received_at=exit_fill_at)
    runner.ingest("s2", exit_config, [exit_quote], ingest_metadata={
        exit_quote.event_id: (exit_fill_at + timedelta(milliseconds=1), "TEST_FILL_QUOTE_CLOCK")})
    exit_receipt = runner.execute_paper_intent(
        session_id="s2", order_id="book-exit", decision_id=exit_decision_id,
        event_id=exit_quote.event_id, side="SELL", quantity=signal.shares,
        limit_price=Decimal("94.00"), tick_size=Decimal("0.05"), freeze_limit=10,
        slice_size=10, adverse_slippage_bps=Decimal("0"), latency_ms=100,
        simulated_at=exit_fill_at + timedelta(milliseconds=50))
    assert exit_receipt["filled_quantity"] == signal.shares
    assert reconcile_exit_receipt(book=book, runner=runner, session_id="s2", exit_signal=exits[0],
                                  decision_id=exit_decision_id, order_id="book-exit",
                                  receipt=exit_receipt) == "CLOSED"
    assert reconcile_exit_receipt(book=book, runner=runner, session_id="s2", exit_signal=exits[0],
                                  decision_id=exit_decision_id, order_id="book-exit",
                                  receipt=exit_receipt) == "CLOSED"
    assert book.execute("SELECT exit_day,exit_reason,held FROM positions WHERE version=?", [version]).fetchone() == (
        exit_day, "STOP", 1)
    from financial_brain.paper import book as paper_book
    spec = paper_book.BOOKS[version]
    position_cost = book.execute("SELECT cost FROM positions WHERE version=?", [version]).fetchone()[0]
    bridge_costs = book.execute("SELECT state,simulated_charges_inr FROM paper_book_bridge "
                                "WHERE signal_id=? ORDER BY order_id", [signal.signal_id]).fetchall()
    if with_cost_schedule:
        assert position_cost is not None and position_cost > 0
        assert all(amount is not None and amount > 0 for _, amount in bridge_costs)
        assert paper_book.frame(book, "live", spec).iloc[0]["cost_basis"] == "SIMULATED_SCHEDULE"
    else:
        assert position_cost is None
        assert all(amount is None for _, amount in bridge_costs)
        assert paper_book.frame(book, "live", spec).iloc[0]["cost_basis"] == "UNPRICED"
    summary = paper_book.summary(book, "live", spec)
    assert summary["closed"] == 1
    if with_cost_schedule:
        assert summary["cost_priced_closed"] == 1 and summary["cost_unpriced_closed"] == 0
    else:
        assert summary["cost_unpriced_closed"] == 1
        assert summary["verdict"] == "COST EVIDENCE INCOMPLETE"
    assert runner.records("s2")["accounting"]["positions"] == {}
    runner.close_session("s2", complete=True, now=exit_fill_at + timedelta(seconds=2))
    book.close()
    runner.close()


def test_decision_manifest_and_risk_verdict_replay_idempotently_after_restart(tmp_path):
    runner, _, _ = trade_setup(tmp_path)
    baseline = runner.records("s")
    runner.close()

    resumed = make_runner(tmp_path)
    assert resumed.resume("s")["state"] == "OPEN"
    decision = baseline["decisions"][0]
    assert resumed.decide(session_id="s", strategy_id=decision["strategy_id"],
                          decision_id=decision["decision_id"], outcome=decision["outcome"],
                          input_event_ids=tuple(decision["input_event_ids"]),
                          decided_at=datetime.fromisoformat(decision["decided_at"])) is False
    manifest_row = resumed._con.execute("SELECT manifest_sha256 FROM runner_decision_manifests "
                                        "WHERE session_id='s' AND decision_id='trade-1'").fetchone()
    manifest = baseline["decision_manifests"][0]
    assert resumed.record_decision_manifest(session_id="s", decision_id="trade-1",
                                             manifest=manifest) == manifest_row[0]
    risk = baseline["risk_verdicts"][0]
    assert resumed.record_risk_verdict(
        session_id="s", decision_id=risk["decision_id"], verdict_id=risk["verdict_id"],
        allowed=risk["allowed"], policy_id=risk["policy_id"], policy_sha256=risk["policy_sha256"],
        reason_codes=tuple(risk["reason_codes"]),
        assessed_at=datetime.fromisoformat(risk["assessed_at"])) is False
    assert resumed.records("s")["audit_log"] == baseline["audit_log"]
    resumed.close()


def test_restart_restores_risk_positions_and_cash_without_repeating_paper_fill(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    original_receipt = execute(runner, event, decided)
    before_restart = runner.records("s")
    runner.close()

    resumed = make_runner(tmp_path)
    restored = resumed.resume("s")
    after_restart = restored["records"]
    assert restored["state"] == "OPEN"
    assert after_restart["risk_verdicts"] == before_restart["risk_verdicts"]
    assert after_restart["accounting"]["positions"] == before_restart["accounting"]["positions"]
    assert after_restart["accounting"]["cash_inr"] == before_restart["accounting"]["cash_inr"]
    assert after_restart["execution_receipts"] == [original_receipt]

    repeated = execute(resumed, event, decided)
    assert repeated == original_receipt
    assert len(resumed.records("s")["execution_receipts"]) == 1
    report = resumed.close_session("s", complete=True, now=decided + timedelta(seconds=2))
    assert report["status"] == "CLOSED" and report["execution_receipt_count"] == 1
    resumed.close()


def test_partial_missed_and_rejected_scenarios_keep_requested_vs_filled_separate(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    partial = execute(runner, event, decided, order_id="partial", qty=5)
    assert partial["status"] == "PARTIAL" and partial["filled_quantity"] == 3
    missed = execute(runner, event, decided, order_id="missed", qty=2, limit="100.00")
    assert missed["status"] == "MISSED" and missed["filled_quantity"] == 0
    rejected = execute(runner, event, decided, order_id="freeze", qty=11)
    assert rejected["status"] == "REJECTED" and rejected["filled_quantity"] == 0
    events = runner.records("s")["order_events"]
    states_by_order = {}
    for row in events:
        states_by_order.setdefault(row["paper_order_id"], []).append(row["state"])
    assert states_by_order["partial"] == ["CREATED", "SUBMITTED", "PARTIAL"]
    assert states_by_order["missed"] == ["CREATED", "SUBMITTED", "MISSED"]
    assert states_by_order["freeze"] == ["CREATED", "SUBMITTED", "REJECTED"]
    with pytest.raises(ForwardSessionRunnerError, match=r"unresolved paper order states: \['PARTIAL'\]"):
        runner.close_session("s", complete=True, now=decided + timedelta(seconds=2))
    runner.close()


def test_paper_receipt_records_multi_level_observed_depth_fills(tmp_path):
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
             "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                       "sell": [{"price": 100.05, "quantity": 1},
                                {"price": 100.10, "quantity": 2},
                                {"price": 100.15, "quantity": 20}]}}
    runner, event, decided = trade_setup(tmp_path, quote=quote)
    receipt = execute(runner, event, decided, qty=3, limit="100.30")
    assert receipt["status"] == "FILLED"
    assert receipt["depth_match_mode"] == "OBSERVED_OPPOSING_BOOK_LEVELS"
    assert receipt["filled_quantity"] == 3
    assert receipt["visible_depth_fills"] == [
        {"price": "100.20", "quantity": 1},
        {"price": "100.25", "quantity": 2},
    ]
    assert receipt["observed_opposing_depth_quantity"] == 23
    assert receipt["average_price"] == "100.2333333333333333333333333"
    assert runner.records("s")["accounting"]["positions"] == {"NSE:INFY": 3}
    runner.close()


def test_ltp_only_cannot_create_paper_fill_intent(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    tick = normalize_kite_quote("NSE:INFY", {"timestamp": "2026-11-13 10:15:00",
                                  "last_price": 100.0, "volume": 1000}, received_at=NOW)
    runner.ingest("s", make_config(), [tick])
    decided = NOW + timedelta(seconds=1)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(tick.event_id,))
    record_manifest(runner, session_id="s", decision_id="trade-1", event=tick,
                    decided_at=decided, quantity=2)
    runner.record_risk_verdict(session_id="s", decision_id="trade-1", verdict_id="risk-trade-1",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=decided)
    with pytest.raises(ForwardSessionRunnerError, match="two-sided quote"):
        execute(runner, tick, decided)
    runner.close()


def test_paper_fill_requires_hashed_raw_source_evidence(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
             "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                       "sell": [{"price": 100.05, "quantity": 3}]}}
    event = replace(normalize_kite_quote("NSE:INFY", quote, received_at=NOW),
                    event_id="quote-without-source-hash", evidence_id=None)
    runner.ingest("s", make_config(), [event],
                  ingest_metadata={event.event_id: (NOW + timedelta(milliseconds=10), "FIXTURE_CLOCK")})
    decided = NOW + timedelta(seconds=1)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(event.event_id,))
    record_manifest(runner, session_id="s", decision_id="trade-1", event=event,
                    decided_at=decided, quantity=1)
    runner.record_risk_verdict(session_id="s", decision_id="trade-1", verdict_id="risk-trade-1",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=decided)
    with pytest.raises(ForwardSessionRunnerError, match="SHA-256 source-payload"):
        runner.execute_paper_intent(
            session_id="s", order_id="no-evidence-order", decision_id="trade-1",
            event_id=event.event_id, side="BUY", quantity=1, limit_price=Decimal("101"),
            tick_size=Decimal("0.05"), freeze_limit=10, slice_size=10,
            adverse_slippage_bps=Decimal("10"), latency_ms=100,
            simulated_at=decided + timedelta(milliseconds=100))
    assert runner.records("s")["intents"] == []
    runner.close()


def test_injected_clock_supplies_decision_execution_and_close_times(tmp_path):
    class SequenceClock:
        def __init__(self, values):
            self.values = iter(values)

        def now(self):
            return next(self.values)

    decided_at = NOW + timedelta(seconds=1)
    executed_at = decided_at + timedelta(milliseconds=100)
    closed_at = executed_at + timedelta(seconds=1)
    clock = SequenceClock([NOW, decided_at, executed_at, closed_at])
    runner = ForwardSessionRunner(ForwardSessionStore(tmp_path / "state.sqlite"),
                                  tmp_path / "journal.sqlite", clock=clock)
    assert runner.begin("clock-session", make_config(),
                        preflight={**READY_PREFLIGHT, "risk_policy_loaded": True}) is SessionState.OPEN
    signal_event = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
        "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                  "sell": [{"price": 100.05, "quantity": 3}]},
    }, received_at=NOW)
    runner.ingest("clock-session", make_config(), [signal_event],
                  ingest_metadata={signal_event.event_id: (NOW + timedelta(milliseconds=10), "FIXTURE_CLOCK")})
    runner.decide(session_id="clock-session", strategy_id="momentum", decision_id="clock-trade",
                  outcome="TRADE", input_event_ids=(signal_event.event_id,))
    record_manifest(runner, session_id="clock-session", decision_id="clock-trade", event=signal_event,
                    decided_at=decided_at, quantity=2)
    runner.record_risk_verdict(session_id="clock-session", decision_id="clock-trade",
                               verdict_id="clock-risk", allowed=True, policy_id="test-risk-v1",
                               policy_sha256="a" * 64, assessed_at=decided_at)
    fill_event = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
        "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                  "sell": [{"price": 100.05, "quantity": 3}]},
    }, received_at=executed_at)
    runner.ingest("clock-session", make_config(), [fill_event],
                  ingest_metadata={fill_event.event_id: (executed_at, "FIXTURE_FILL_CLOCK")})
    receipt = runner.execute_paper_intent(
        session_id="clock-session", order_id="clock-order", decision_id="clock-trade",
        event_id=fill_event.event_id, side="BUY", quantity=2, limit_price=Decimal("100.30"),
        tick_size=Decimal("0.05"), freeze_limit=10, slice_size=10,
        adverse_slippage_bps=Decimal("10"), latency_ms=100)
    assert receipt["simulated_at"] == executed_at.isoformat()
    report = runner.close_session("clock-session", complete=True)
    assert report["closed_at"] == closed_at.isoformat()
    runner.close()


def test_unknown_outbox_after_crash_requires_receipt_reconciliation(tmp_path, monkeypatch):
    from financial_brain.kernel.events import UnknownOutcome

    runner, event, decided = trade_setup(tmp_path)
    persist = runner._persist_paper_receipt

    def crash_after_persist(session_id, order_id, body, *, commit=True):
        persist(session_id, order_id, body, commit=commit)
        raise RuntimeError("injected crash before outbox acknowledgement")

    monkeypatch.setattr(runner, "_persist_paper_receipt", crash_after_persist)
    with pytest.raises(UnknownOutcome):
        execute(runner, event, decided)
    monkeypatch.setattr(runner, "_persist_paper_receipt", persist)
    with pytest.raises(UnknownOutcome):
        execute(runner, event, decided)
    with pytest.raises(ForwardSessionRunnerError, match="pending or UNKNOWN"):
        runner.close_session("s", now=decided + timedelta(seconds=1), complete=True)
    runner.close()
    runner = make_runner(tmp_path)
    recovery = runner.resume("s")
    assert recovery["state"] == "OPEN"
    assert recovery["manual_reconciliation_required"] is True
    assert recovery["unresolved_paper_outbox_keys"] == ["paper:s:order-1"]
    assert runner.reconcile_unknown_paper_intent(session_id="s", order_id="order-1",
                                                  reconciled_by="local-receipt-check") is True
    assert [row["state"] for row in runner.records("s")["order_events"]] == [
        "CREATED", "SUBMITTED", "UNKNOWN", "FILLED"]
    receipt = execute(runner, event, decided)
    assert receipt["status"] == "FILLED"
    assert runner._con.execute("SELECT COUNT(*) FROM paper_execution_receipts").fetchone()[0] == 1
    report = runner.close_session("s", now=decided + timedelta(seconds=1), complete=True)
    assert report["status"] == "CLOSED" and report["execution_receipt_count"] == 1
    runner.close()


def test_restart_materializes_unknown_order_state_if_process_dies_before_order_ack(tmp_path, monkeypatch):
    runner, event, decided = trade_setup(tmp_path)
    persist = runner._persist_paper_receipt
    append_order_event = runner._append_paper_order_event

    def persist_then_crash(session_id, order_id, body, *, commit=True):
        persist(session_id, order_id, body, commit=commit)
        raise RuntimeError("simulated crash before outbox acknowledgement")

    def crash_before_unknown_journal(**kwargs):
        if kwargs["state"] == "UNKNOWN":
            raise RuntimeError("simulated process death while recording UNKNOWN")
        return append_order_event(**kwargs)

    monkeypatch.setattr(runner, "_persist_paper_receipt", persist_then_crash)
    monkeypatch.setattr(runner, "_append_paper_order_event", crash_before_unknown_journal)
    with pytest.raises(RuntimeError, match="process death"):
        execute(runner, event, decided)
    assert [row["state"] for row in runner.records("s")["order_events"]] == ["CREATED", "SUBMITTED"]
    runner.close()

    recovered = make_runner(tmp_path)
    recovery = recovered.resume("s")
    assert recovery["manual_reconciliation_required"] is True
    assert [row["state"] for row in recovery["records"]["order_events"]] == [
        "CREATED", "SUBMITTED", "UNKNOWN"]
    assert recovered.reconcile_unknown_paper_intent(
        session_id="s", order_id="order-1", reconciled_by="receipt-row-check") is True
    assert [row["state"] for row in recovered.records("s")["order_events"]] == [
        "CREATED", "SUBMITTED", "UNKNOWN", "FILLED"]
    recovered.close()


def test_restart_rebuilds_terminal_order_state_from_dispatched_receipt(tmp_path, monkeypatch):
    runner, event, decided = trade_setup(tmp_path)

    def crash_before_lifecycle_commit(**_kwargs):
        raise RuntimeError("simulated crash after outbox dispatch")

    monkeypatch.setattr(runner, "_append_paper_receipt_outcome", crash_before_lifecycle_commit)
    with pytest.raises(RuntimeError, match="after outbox dispatch"):
        execute(runner, event, decided)
    assert runner._con.execute(
        "SELECT state FROM outbox WHERE key='paper:s:order-1'").fetchone()[0] == "DISPATCHED"
    assert [row["state"] for row in runner.records("s")["order_events"]] == ["CREATED", "SUBMITTED"]
    runner.close()

    recovered = make_runner(tmp_path)
    recovery = recovered.resume("s")
    assert recovery["unresolved_paper_outbox_keys"] == []
    assert [row["state"] for row in recovery["records"]["order_events"]] == [
        "CREATED", "SUBMITTED", "FILLED"]
    recovered.close()


def test_paper_receipt_reopens_in_a_distinct_process_without_duplicate_fill(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    first = execute(runner, event, decided)
    assert first["status"] == "FILLED"
    assert len(runner.records("s")["execution_receipts"]) == 1
    runner.close()

    script = """
import json, os, sys
from pathlib import Path
from financial_brain.forward_edge.session_runner import ForwardSessionRunner
from financial_brain.forward_edge.session_state import ForwardSessionStore
root = Path(sys.argv[1])
runner = ForwardSessionRunner(ForwardSessionStore(root / 'state.sqlite'), root / 'journal.sqlite')
recovery = runner.resume('s')
records = runner.records('s')
print(json.dumps({'pid': os.getpid(), 'state': recovery['state'],
                  'manual_reconciliation_required': recovery['manual_reconciliation_required'],
                  'receipt_count': len(records['execution_receipts']),
                  'filled_quantities': [row['filled_quantity'] for row in records['execution_receipts']]}))
runner.close()
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path.cwd() / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=Path.cwd(), env=environment,
                            check=True, capture_output=True, text=True, timeout=30)
    recovered = json.loads(result.stdout.strip().splitlines()[-1])
    assert recovered["pid"] != os.getpid()
    assert recovered["state"] == "OPEN"
    assert recovered["manual_reconciliation_required"] is False
    assert recovered["receipt_count"] == 1
    assert recovered["filled_quantities"] == [2]


def test_crash_before_paper_receipt_reconciles_no_effect_then_creates_one_receipt(tmp_path, monkeypatch):
    from financial_brain.kernel.events import UnknownOutcome

    runner, event, decided = trade_setup(tmp_path)

    def crash_before_persist(*_args, **_kwargs):
        raise RuntimeError("injected crash before simulation receipt")

    monkeypatch.setattr(runner, "_persist_paper_receipt", crash_before_persist)
    with pytest.raises(UnknownOutcome):
        execute(runner, event, decided)
    assert runner._con.execute("SELECT COUNT(*) FROM paper_execution_receipts").fetchone()[0] == 0
    incomplete = runner.records("s")["accounting"]
    assert incomplete["status"] == "INCOMPLETE" and incomplete["completed_order_ids"] == []
    with pytest.raises(ForwardSessionRunnerError, match="accounting projection is incomplete"):
        execute(runner, event, decided)
    runner.close()

    runner = make_runner(tmp_path)
    recovery = runner.resume("s")
    assert recovery["manual_reconciliation_required"] is True
    assert runner.reconcile_unknown_paper_intent(session_id="s", order_id="order-1",
                                                  reconciled_by="local-receipt-check") is False
    assert [row["state"] for row in runner.records("s")["order_events"]] == [
        "CREATED", "SUBMITTED", "UNKNOWN", "SUBMITTED"]
    receipt = execute(runner, event, decided)
    assert receipt["status"] == "FILLED"
    assert runner._con.execute("SELECT COUNT(*) FROM paper_execution_receipts").fetchone()[0] == 1
    assert len(runner.records("s")["execution_receipts"]) == 1
    runner.close()
