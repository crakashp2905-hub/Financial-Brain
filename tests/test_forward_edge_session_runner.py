from datetime import date, datetime, time, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import duckdb
import pytest

from financial_brain.forward_edge.market_data import KiteQuoteNormalizationError, normalize_kite_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.durable import DurableJournalError
from financial_brain.forward_edge.instrument_rules import fixture_rule_book
from financial_brain.forward_edge.session_runner import (
    ForwardSessionRunner,
    ForwardSessionRunnerError,
)
from financial_brain.forward_edge.trader_dashboard import snapshots_from_forward_sessions
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
    SessionStateError,
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
                   "carry_authorized_instruments": ["NSE:INFY"],
                   "instrument_rules": fixture_rule_book("NSE:INFY"),
                   "data_permissions": [{"source_id": "KITE_CONNECT_READ_ONLY",
                                        "use": "PAPER_SIMULATION", "status": "USE_ALLOWED",
                                        "effective_from": "2026-11-13", "effective_through": None,
                                        "evidence_ref": "tests/fixture-kite-permission",
                                        "reviewer": "test-fixture"}],
                   "reservation_policy": {
                       "version": "test-paper-reservation-v1",
                       "frozen_at": "2026-11-12T00:00:00+00:00",
                       "source_sha256": "b" * 64,
                       "limits": {"CASH": "1000000000",
                                  "MARGIN": "1000000000", "RISK_CAPITAL": "1000000000",
                                  "OPTION_BUYING_POWER": "1000000000",
                                  "PAPER_COST_BUFFER": "1000000000",
                                  "INSTRUMENT_QUANTITY:NSE:INFY": "1000000",
                                  "SELLABLE_INVENTORY:NSE:INFY": "1000000"},
                   }}
    if max_quote_age_ms is not None:
        constraints["max_quote_age_ms"] = max_quote_age_ms
    return ForwardRunConfig(date(2026, 11, 13), "cohort-v1", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"), constraints, "ledger-1")


def make_live_paper_config(*, include_prospective_rights=False):
    from dataclasses import replace
    from financial_brain.costs.india import CostModel

    base = make_config()
    parameters = {"lookback_bars": 20, "entry_threshold": "0.75"}
    risk_limits = {"daily_loss_inr": "2500", "max_order_value_inr": "100000"}
    constraints = dict(base.constraints)
    constraints.update({
        "strategy_parameters": parameters,
        "strategy_parameters_sha256": hashlib.sha256(json.dumps(
            parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "risk_limits": risk_limits,
        "signal_interval_seconds": 60,
        "cost_schedule": {
            "schedule_id": "test-india", "version": "v1", "effective_from": "2026-01-01",
            "effective_to": None, "segment": "delivery", "model": CostModel().as_dict(),
            "source_ref": "test tariff fixture", "source_sha256": "c" * 64,
        },
    })
    if include_prospective_rights:
        constraints["data_permissions"] = [*constraints["data_permissions"], {
            "source_id": "KITE_CONNECT_READ_ONLY", "use": "PROSPECTIVE_PAPER_CAPTURE",
            "status": "USE_ALLOWED", "effective_from": "2026-11-13", "effective_through": None,
            "evidence_ref": "test-only-explicit-provider-approval", "reviewer": "test-owner"}]
    return replace(base, mode="LIVE_PAPER", constraints=constraints)


def make_config_with_constraints(constraints):
    constraints = {"session_phase": "CONTINUOUS",
                   "allow_synthetic_fixture_events": True,
                   "carry_authorized_instruments": ["NSE:INFY"],
                   "data_permissions": [{"source_id": "KITE_CONNECT_READ_ONLY",
                                        "use": "PAPER_SIMULATION", "status": "USE_ALLOWED",
                                        "effective_from": "2026-11-13", "effective_through": None,
                                        "evidence_ref": "tests/fixture-kite-permission",
                                        "reviewer": "test-fixture"}],
                   "instrument_rules": fixture_rule_book("NSE:INFY"),
                   "reservation_policy": {
                       "version": "test-paper-reservation-v1",
                       "frozen_at": "2026-11-12T00:00:00+00:00",
                       "source_sha256": "b" * 64,
                       "limits": {"CASH": "1000000000",
                                  "MARGIN": "1000000000", "RISK_CAPITAL": "1000000000",
                                  "OPTION_BUYING_POWER": "1000000000",
                                  "PAPER_COST_BUFFER": "1000000000",
                                  "INSTRUMENT_QUANTITY:NSE:INFY": "1000000",
                                  "SELLABLE_INVENTORY:NSE:INFY": "1000000"},
                   }, **constraints}
    return ForwardRunConfig(date(2026, 11, 13), "cohort-v1", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"), constraints, "ledger-1")


def make_profiled_config(profile="EQUITY_EXECUTION"):
    return make_config_with_constraints({"data_profiles": [
        {"instrument_id": "NSE:INFY", "profile": profile}
    ]})


def test_event_loop_requires_a_profile_for_every_frozen_instrument(tmp_path):
    runner = make_runner(tmp_path)
    with pytest.raises(ForwardSessionRunnerError, match="requires an explicit data profile"):
        runner.run_event_loop(
            session_id="unprofiled", config=make_config(), events=[],
            session_open_at=NOW, preflight=READY_PREFLIGHT,
            consumer_id="strategy:unprofiled", on_market_event=lambda *_: None,
            evaluate_protective_exits=lambda *_: None)
    with pytest.raises(SessionStateError, match="unknown forward session"):
        runner.state_store.state("unprofiled")
    runner.close()


def test_market_ingest_rejects_mutated_or_replaced_session_configuration(tmp_path):
    from dataclasses import replace

    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event()
    changed = replace(config, capital=Decimal("200000"))
    with pytest.raises(ForwardSessionRunnerError, match="differs from the frozen session configuration"):
        runner.ingest("s", changed, [event])
    assert runner.records("s")["events"] == []
    runner.close()


def make_event(*, event_id="fixture-1", available_at=NOW, event_time=NOW):
    return MarketEvent(event_id, EventKind.QUOTE, "NSE", "NSE:INFY", event_time,
                       available_at, available_at, {"bid": 100.0, "ask": 100.1}, "FIXTURE")


def test_deterministic_event_loop_orders_events_and_checks_exits_after_each(tmp_path):
    runner = make_runner(tmp_path)
    config = make_profiled_config()
    later = make_event(event_id="later", available_at=NOW + timedelta(seconds=2),
                       event_time=NOW + timedelta(seconds=2))
    earlier = make_event(event_id="earlier", available_at=NOW + timedelta(seconds=1),
                         event_time=NOW + timedelta(seconds=1))
    callbacks = []
    result = runner.run_event_loop(
        session_id="deterministic", config=config, events=[later, earlier],
        session_open_at=NOW - timedelta(seconds=1), preflight=READY_PREFLIGHT,
        consumer_id="strategy:all-events",
        on_market_event=lambda event, _: callbacks.append(("market", event.event_id)),
        evaluate_protective_exits=lambda event, _: callbacks.append(("exit", event.event_id)),
        on_close=lambda _: callbacks.append(("close", "session")),
        session_close_at=NOW + timedelta(seconds=3), close_complete=True)

    assert callbacks == [("market", "earlier"), ("exit", "earlier"),
                         ("market", "later"), ("exit", "later"), ("close", "session")]
    assert result["persisted_event_ids"] == ["earlier", "later"]
    assert result["processed_event_ids"] == ["earlier", "later"]
    assert result["status"] == "CLOSED"
    runner.close()


def test_event_loop_replays_unacknowledged_event_after_restart(tmp_path):
    config = make_profiled_config()
    config = replace(config, constraints={**config.constraints,
                                           "handler_retry_base_delay_ms": 0,
                                           "handler_retry_cap_delay_ms": 0})
    events = [make_event(event_id=f"restart-{index}",
                         available_at=NOW + timedelta(seconds=index),
                         event_time=NOW + timedelta(seconds=index)) for index in range(2)]
    runner = make_runner(tmp_path)
    attempted = []

    def fail_on_second(event, _):
        attempted.append(event.event_id)
        if event.event_id == "restart-1":
            raise RuntimeError("simulated crash before cursor acknowledgement")

    with pytest.raises(RuntimeError, match="simulated crash"):
        runner.run_event_loop(
            session_id="restartable", config=config, events=events,
            session_open_at=NOW - timedelta(seconds=1), preflight=READY_PREFLIGHT,
            consumer_id="strategy:restartable", on_market_event=fail_on_second,
            evaluate_protective_exits=lambda *_: None)
    assert runner.state_store.state("restartable") is SessionState.OPEN
    assert runner.event_cursor("restartable", "strategy:restartable")["event_id"] == "restart-0"
    runner.close()

    resumed = make_runner(tmp_path)
    replayed = []
    result = resumed.run_event_loop(
        session_id="restartable", config=config, events=[],
        session_open_at=NOW - timedelta(seconds=1), preflight=READY_PREFLIGHT,
        consumer_id="strategy:restartable",
        on_market_event=lambda event, _: replayed.append(("market", event.event_id)),
        evaluate_protective_exits=lambda event, _: replayed.append(("exit", event.event_id)),
        session_close_at=NOW + timedelta(seconds=3), close_complete=True)
    assert attempted == ["restart-0", "restart-1"]
    assert replayed == [("market", "restart-1"), ("exit", "restart-1")]
    recovery = resumed.records("restartable")["handler_recovery_events"]
    assert [(row["handler"], row["status"]) for row in recovery] == [
        ("market", "RETRY_WAIT"), ("market", "RECOVERED")]
    assert resumed.records("restartable")["operating_mode"] == "DEGRADED"
    assert result["status"] == "CLOSED"
    resumed.close()


def test_event_loop_defers_retry_without_acknowledging_event(tmp_path):
    config = make_profiled_config()
    config = replace(config, constraints={**config.constraints,
                                           "handler_retry_base_delay_ms": 60_000,
                                           "handler_retry_cap_delay_ms": 60_000})
    runner = make_runner(tmp_path)
    event = make_event(event_id="deferred-retry")
    with pytest.raises(RuntimeError, match="temporary monitor failure"):
        runner.run_event_loop(
            session_id="defer", config=config, events=[event], session_open_at=NOW - timedelta(seconds=1),
            preflight=READY_PREFLIGHT, consumer_id="position-monitor",
            on_market_event=lambda *_: None,
            evaluate_protective_exits=lambda *_: (_ for _ in ()).throw(RuntimeError("temporary monitor failure")))
    with pytest.raises(ForwardSessionRunnerError, match="handler retry deferred until"):
        runner.run_event_loop(
            session_id="defer", config=config, events=[], session_open_at=NOW - timedelta(seconds=1),
            preflight=READY_PREFLIGHT, consumer_id="position-monitor",
            on_market_event=lambda *_: None, evaluate_protective_exits=lambda *_: None)

    assert runner.event_cursor("defer", "position-monitor") is None
    assert runner.records("defer")["handler_recovery_events"][0]["status"] == "RETRY_WAIT"
    assert runner.records("defer")["operating_mode"] == "DEGRADED"
    runner.close()


def test_event_loop_halts_after_handler_retry_budget_is_exhausted(tmp_path):
    config = make_profiled_config()
    config = replace(config, constraints={**config.constraints,
                                           "handler_retry_max_attempts": 1,
                                           "handler_retry_base_delay_ms": 0,
                                           "handler_retry_cap_delay_ms": 0})
    runner = make_runner(tmp_path)
    event = make_event(event_id="halted-handler")
    with pytest.raises(RuntimeError, match="persistent monitor failure"):
        runner.run_event_loop(
            session_id="halt", config=config, events=[event], session_open_at=NOW - timedelta(seconds=1),
            preflight=READY_PREFLIGHT, consumer_id="position-monitor",
            on_market_event=lambda *_: None,
            evaluate_protective_exits=lambda *_: (_ for _ in ()).throw(RuntimeError("persistent monitor failure")))

    assert runner.state_store.state("halt") is SessionState.OPEN
    assert runner.records("halt")["operating_mode"] == "HALTED"
    assert runner.event_cursor("halt", "position-monitor") is None
    with pytest.raises(ForwardSessionRunnerError, match="circuit breaker is HALTED"):
        runner.run_event_loop(
            session_id="halt", config=config, events=[], session_open_at=NOW - timedelta(seconds=1),
            preflight=READY_PREFLIGHT, consumer_id="position-monitor",
            on_market_event=lambda *_: None, evaluate_protective_exits=lambda *_: None)
    runner.close()


def test_event_loop_separates_completed_bar_forecasts_from_continuous_exit_checks(tmp_path):
    runner = make_runner(tmp_path)
    bar_start = datetime.fromisoformat("2026-11-13T10:15:00+05:30")
    bar_end = datetime.fromisoformat("2026-11-13T10:16:00+05:30")
    completed = MarketEvent(
        "completed-bar", EventKind.BAR, "NSE", "NSE:INFY", bar_end,
        bar_end + timedelta(seconds=1), bar_end + timedelta(seconds=1),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 20,
         "complete": True, "interval_seconds": 60,
         "bar_start_at": bar_start.isoformat(), "bar_end_at": bar_end.isoformat()}, "FIXTURE")
    partial = MarketEvent(
        "partial-bar", EventKind.BAR, "NSE", "NSE:INFY", NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=1), NOW + timedelta(seconds=1),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10,
         "complete": False}, "FIXTURE")
    quote = make_event(event_id="interbar-quote", available_at=NOW, event_time=NOW)
    market_events = []
    forecast_bars = []
    exit_checks = []
    runner.run_event_loop(
        session_id="cadence", config=make_profiled_config(), events=[completed, partial, quote],
        session_open_at=NOW - timedelta(seconds=1), preflight=READY_PREFLIGHT,
        consumer_id="strategy:cadence",
        on_market_event=lambda event, _: market_events.append(event.event_id),
        on_completed_bar=lambda event, _: forecast_bars.append(event.event_id),
        venue_calendar=_calendar(date(2026, 11, 13)), venue="NSE",
        evaluate_protective_exits=lambda event, _: exit_checks.append(event.event_id))

    assert market_events == ["interbar-quote", "partial-bar", "completed-bar"]
    assert forecast_bars == ["completed-bar"]
    assert exit_checks == market_events
    runner.close()


def test_completed_bar_rejects_interval_not_aligned_to_sourced_session_open(tmp_path):
    runner = make_runner(tmp_path)
    start = datetime.fromisoformat("2026-11-13T10:16:00+05:30")
    end = start + timedelta(minutes=15)
    event = MarketEvent(
        "misaligned-15m", EventKind.BAR, "NSE", "NSE:INFY", end,
        end + timedelta(seconds=1), end + timedelta(seconds=1),
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 20,
         "complete": True, "interval_seconds": 900,
         "bar_start_at": start.isoformat(), "bar_end_at": end.isoformat()}, "FIXTURE")
    with pytest.raises(ForwardSessionRunnerError, match="not aligned to the official exchange session open"):
        runner.run_event_loop(
            session_id="misaligned", config=make_profiled_config(), events=[event],
            session_open_at=NOW - timedelta(seconds=1), preflight=READY_PREFLIGHT,
            consumer_id="strategy:misaligned", on_market_event=lambda *_: None,
            evaluate_protective_exits=lambda *_: None, on_completed_bar=lambda *_: None,
            venue_calendar=_calendar(date(2026, 11, 13)), venue="NSE")
    assert runner.records("misaligned")["events"] == []
    runner.close()


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


def test_configured_mutual_fund_nav_profile_rejects_quote_feed_event(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config_with_constraints({"data_profiles": [
        {"instrument_id": "NSE:INFY", "profile": "MUTUAL_FUND_NAV"}
    ]})
    opened(runner, config=config)
    with pytest.raises(ForwardSessionRunnerError, match="requires an NAV observation"):
        runner.ingest("s", config, [make_event()])
    assert runner.records("s")["events"] == []
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


def test_end_checkpoint_hashes_event_consumer_offsets(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event(event_id="checkpoint-offset")
    runner.ingest("s", config, [event])
    [record] = runner.read_events_after("s", "strategy:momentum")
    runner.acknowledge_event_offset("s", "strategy:momentum", expected_offset=0,
                                    through_offset=record["offset"], event_ids=(event.event_id,))
    report = runner.close_session("s", complete=True, now=NOW + timedelta(seconds=1))
    assert report["day_end_reconciliation"]["reconciled"] is True
    assert report["day_end_reconciliation"]["cash_reconciled"] is True
    assert report["day_end_reconciliation"]["marked_equity_inr"] == "100000"
    assert report["day_end_reconciliation"]["open_risk_inr"] == "0"

    checkpoint = runner.checkpoint("s", phase="END", now=NOW + timedelta(seconds=2))
    assert "event_cursors" in checkpoint["record_hashes"]
    assert runner.verify_checkpoint("s", checkpoint["checkpoint_id"])["current_records_match"] is True
    runner._con.execute("UPDATE runner_event_cursors SET event_id='tampered' WHERE session_id='s'")
    runner._con.commit()
    assert runner.verify_checkpoint("s", checkpoint["checkpoint_id"])["current_records_match"] is False
    runner.close()


def test_dashboard_adapter_consumes_durable_forward_session_close_evidence(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    runner.ingest("s", config, [make_event(event_id="dashboard-quote")])
    runner.close_session("s", complete=True, now=NOW + timedelta(seconds=1))
    (snapshot,) = snapshots_from_forward_sessions(
        runner=runner, desks={"SWING": ("s", "FIXTURE", 10.0)})
    assert snapshot.source_mode == "FIXTURE"
    assert snapshot.source_sha256 == runner.records("s")["session_reports"][0]["report_sha256"]
    assert snapshot.newest_quote_age_seconds == 1.0
    assert snapshot.free_cash_inr == 100000
    assert snapshot.risk_policy_status == "PASS"
    runner.close()


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


def test_missing_or_stale_market_data_is_journaled_as_data_unusable_not_tradable(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    assert runner.decide(
        session_id="s", strategy_id="momentum", decision_id="missing-source",
        outcome="DATA_UNUSABLE", input_event_ids=(), decided_at=NOW + timedelta(seconds=1),
        reason_codes=("MISSING_REQUIRED_SOURCE",),
    )

    stale = make_event(event_id="stale-quality-source", available_at=NOW, event_time=NOW)
    runner.ingest("s", config, [stale])
    assert runner.decide(
        session_id="s", strategy_id="momentum", decision_id="stale-source",
        outcome="DATA_UNUSABLE", input_event_ids=(stale.event_id,),
        decided_at=NOW + timedelta(seconds=10), reason_codes=("STALE_SOURCE_DATA",),
    )
    decisions = {row["decision_id"]: row for row in runner.records("s")["decisions"]}
    assert decisions["missing-source"]["outcome"] == "DATA_UNUSABLE"
    assert decisions["stale-source"]["outcome"] == "DATA_UNUSABLE"
    assert runner.records("s")["intents"] == []
    runner.close()


def test_data_unusable_without_source_cannot_claim_computed_features(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    with pytest.raises(ForwardSessionRunnerError, match="without source inputs"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="unverifiable-data-failure",
                      outcome="DATA_UNUSABLE", input_event_ids=(), decided_at=NOW,
                      reason_codes=("MISSING_REQUIRED_SOURCE",), feature_ready_at=NOW)
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
    assert row["as_of"] == event.available_at.astimezone(timezone.utc).isoformat()
    assert row["first_executable_after"] is None
    assert row["expires_at"] is None
    assert row["execution_method"] == []
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


def test_decision_timing_records_feature_inference_and_durable_ledger_clocks(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event(event_id="stage-clock-input")
    runner.ingest("s", config, [event])
    feature_started = NOW + timedelta(milliseconds=1)
    feature_ready = NOW + timedelta(milliseconds=2)
    inference_started = NOW + timedelta(milliseconds=3)
    inference_completed = NOW + timedelta(milliseconds=4)
    decided = NOW + timedelta(milliseconds=5)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="stage-clock-decision",
                  outcome="NO_TRADE", input_event_ids=(event.event_id,), decided_at=decided,
                  reason_codes=("NO_EDGE",), feature_started_at=feature_started,
                  feature_ready_at=feature_ready, model_inference_started_at=inference_started,
                  model_inference_completed_at=inference_completed)

    signal = runner.records("s")["timing_telemetry"]["signals"][0]
    assert signal["feature_computation_started_at"] == feature_started.isoformat()
    assert signal["feature_ready_at"] == feature_ready.isoformat()
    assert signal["model_inference_started_at"] == inference_started.isoformat()
    assert signal["model_inference_completed_at"] == inference_completed.isoformat()
    assert signal["signal_decision_at"] == decided.isoformat()
    assert signal["decision_ledger_recorded_at"] == decided.isoformat()
    assert "MODEL_INFERENCE_TIMESTAMPS" not in signal["missing_stages"]
    runner.close()


def test_decide_rejects_model_inference_clock_regression(tmp_path):
    runner = make_runner(tmp_path)
    config = make_config()
    opened(runner, config=config)
    event = make_event(event_id="invalid-inference-clock-input")
    runner.ingest("s", config, [event])
    with pytest.raises(ForwardSessionRunnerError, match="completion cannot precede its start"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="invalid-inference-clock",
                      outcome="NO_TRADE", input_event_ids=(event.event_id,), decided_at=NOW + timedelta(seconds=1),
                      reason_codes=("NO_EDGE",), model_inference_started_at=NOW + timedelta(milliseconds=5),
                      model_inference_completed_at=NOW + timedelta(milliseconds=4))
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


def test_paper_execution_is_rejected_at_sourced_exchange_close(tmp_path):
    config = make_config(max_quote_age_ms=5000)
    calendar = _calendar(config.exchange_date, closes=time(10, 16))
    runner = make_runner(tmp_path)
    assert runner.plan_calendar_day("cutoff", config, venue="NSE", calendar=calendar,
                                   now=NOW, preflight=READY_PREFLIGHT) is SessionState.OPEN
    signal = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:00", "last_price": 100.05, "volume": 100,
        "depth": {"buy": [{"price": 100.0, "quantity": 20}],
                  "sell": [{"price": 100.1, "quantity": 20}]},
    }, received_at=NOW)
    runner.ingest("cutoff", config, [signal])
    decided = NOW + timedelta(seconds=1)
    runner.decide(session_id="cutoff", strategy_id="momentum", decision_id="cutoff-trade",
                  outcome="TRADE", input_event_ids=(signal.event_id,), decided_at=decided)
    record_manifest(runner, session_id="cutoff", decision_id="cutoff-trade", event=signal,
                    decided_at=decided, quantity=2)
    runner.record_risk_verdict(session_id="cutoff", decision_id="cutoff-trade", verdict_id="cutoff-risk",
                               allowed=True, policy_id="test-risk-v1", policy_sha256="a" * 64,
                               assessed_at=decided)
    fill_quote = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:59", "last_price": 100.05, "volume": 100,
        "depth": {"buy": [{"price": 100.0, "quantity": 20}],
                  "sell": [{"price": 100.1, "quantity": 20}]},
    }, received_at=NOW + timedelta(seconds=59))
    runner.ingest("cutoff", config, [fill_quote])

    with pytest.raises(ForwardSessionRunnerError, match="outside the sourced exchange session cutoff"):
        runner.execute_paper_intent(
            session_id="cutoff", order_id="after-close", decision_id="cutoff-trade",
            event_id=fill_quote.event_id, side="BUY", quantity=2, limit_price=Decimal("100.1"),
            tick_size=Decimal("0.05"), freeze_limit=1000, slice_size=100,
            adverse_slippage_bps=Decimal("0"), latency_ms=10,
            simulated_at=NOW + timedelta(seconds=60), max_wait_ms=1000)
    assert runner.records("cutoff")["intents"] == []
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


def test_paper_session_integrated_shock_drill_preserves_exposure_and_order_truth(tmp_path):
    config = make_config()
    rules = fixture_rule_book("NSE:INFY", quantity_unit="OPTION_CONTRACTS",
                              expiry=config.exchange_date)
    config = replace(config, constraints={**config.constraints, "instrument_rules": rules,
                                          "carry_authorized_instruments": ["NSE:INFY"],
                                          "position_accounting": {"NSE:INFY": {
                                              "asset_class": "OPTION", "currency": "INR",
                                              "multiplier": "1"}}})
    runner = make_runner(tmp_path)
    assert runner.plan_calendar_day(
        "shock", config, venue="NSE", calendar=_calendar(config.exchange_date), now=NOW,
        preflight={**READY_PREFLIGHT, "risk_policy_loaded": True}) is SessionState.OPEN

    quote_payload = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
                     "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                               "sell": [{"price": 100.05, "quantity": 20}]}}
    signal_quote = normalize_kite_quote("NSE:INFY", quote_payload, received_at=NOW)
    runner.ingest("shock", config, [signal_quote])
    decided = NOW + timedelta(seconds=1)
    runner.decide(session_id="shock", strategy_id="shock-drill", decision_id="shock-entry",
                  outcome="TRADE", input_event_ids=(signal_quote.event_id,), decided_at=decided)
    record_manifest(runner, session_id="shock", decision_id="shock-entry", event=signal_quote,
                    decided_at=decided, quantity=2, holding_sessions=1)
    runner.record_risk_verdict(session_id="shock", decision_id="shock-entry", verdict_id="shock-risk",
                               allowed=True, policy_id="shock-test-v1", policy_sha256="a" * 64,
                               assessed_at=decided)
    event_count_before_skew = len(runner.records("shock")["events"])
    fill_time = decided + timedelta(milliseconds=100)
    fill_quote = normalize_kite_quote(
        "NSE:INFY", quote_payload | {"timestamp": "2026-11-13 10:15:01"}, received_at=fill_time)
    runner.ingest("shock", config, [fill_quote], ingest_metadata={
        fill_quote.event_id: (fill_time + timedelta(milliseconds=1), "SHOCK_DRILL_CLOCK")})
    receipt = runner.execute_paper_intent(
        session_id="shock", order_id="shock-entry-order", decision_id="shock-entry",
        event_id=fill_quote.event_id, side="BUY", quantity=2, quantity_unit="OPTION_CONTRACTS",
        limit_price=Decimal("100.10"), tick_size=Decimal("0.05"), freeze_limit=1000,
        slice_size=100, adverse_slippage_bps=Decimal("0"), latency_ms=100,
        simulated_at=decided + timedelta(milliseconds=200))
    assert receipt["status"] == "FILLED" and receipt["filled_quantity"] == 2

    future_quote = quote_payload | {"timestamp": "2026-11-13 10:15:10"}
    with pytest.raises(KiteQuoteNormalizationError, match="future-skew tolerance"):
        normalize_kite_quote("NSE:INFY", future_quote, received_at=NOW + timedelta(seconds=2))
    assert runner.state_store.state("shock") is SessionState.OPEN
    assert len(runner.records("shock")["events"]) == event_count_before_skew + 1
    with pytest.raises(ForwardSessionRunnerError, match="outside frozen universe"):
        runner.ingest("shock", config, [MarketEvent(
            "unmapped-token", EventKind.QUOTE, "NSE", "NSE:UNKNOWN", NOW + timedelta(seconds=3),
            NOW + timedelta(seconds=3), NOW + timedelta(seconds=3),
            {"bid": 1.0, "ask": 1.1}, "SHOCK_DRILL")])
    assert runner.records("shock")["data_rejections"][-1]["source_event_ids"] == ["unmapped-token"]

    # Restart the same durable paper session before processing cancellation evidence.
    runner.close()
    runner = make_runner(tmp_path)
    resumed = runner.resume("shock", now=NOW + timedelta(seconds=4))
    assert resumed["state"] == "OPEN"
    assert len(resumed["records"]["execution_receipts"]) == 1
    assert resumed["records"]["accounting"]["positions"] == {"NSE:INFY": 2}

    for order_id in ("shock-stop", "shock-target"):
        runner._append_paper_order_event(session_id="shock", order_id=order_id, state="CREATED",
                                         occurred_at=NOW, quantity=2, filled_quantity=0)
        runner._append_paper_order_event(session_id="shock", order_id=order_id, state="SUBMITTED",
                                         occurred_at=NOW + timedelta(seconds=1),
                                         quantity=2, filled_quantity=0)
    runner.arm_paper_oco(session_id="shock", group_id="shock-position", stop_order_id="shock-stop",
                         target_order_id="shock-target", occurred_at=NOW + timedelta(seconds=2),
                         evidence_id="b" * 64)
    runner._append_paper_order_event(
        session_id="shock", order_id="shock-stop", state="FILLED", occurred_at=NOW + timedelta(seconds=5),
        quantity=2, filled_quantity=2, price="99.00", fill_evidence_id="c" * 64)
    stop_fill_id = runner.order_journal.replay("shock::shock-stop")[-1]["event_id"]
    cancel_requested = runner.resolve_paper_oco_fill(
        session_id="shock", group_id="shock-position", triggered_order_id="shock-stop",
        fill_event_id=stop_fill_id, occurred_at=NOW + timedelta(seconds=6),
        reconciliation_evidence_id="d" * 64)
    assert cancel_requested.event == "CANCEL_REQUESTED"
    runner._append_paper_order_event(
        session_id="shock", order_id="shock-target", state="CANCELED",
        occurred_at=NOW + timedelta(seconds=7), quantity=2, filled_quantity=0)
    cancel_confirmed = runner.resolve_paper_oco_fill(
        session_id="shock", group_id="shock-position", triggered_order_id="shock-stop",
        fill_event_id=stop_fill_id, occurred_at=NOW + timedelta(seconds=8),
        reconciliation_evidence_id="d" * 64)
    assert cancel_confirmed.event == "CANCEL_CONFIRMED"

    outage = runner.supervise_open_sessions(now=NOW + timedelta(seconds=200),
                                            max_feed_silence=timedelta(seconds=90))
    assert outage[0]["session_id"] == "shock" and outage[0]["state"] == "INCOMPLETE"
    records = runner.records("shock")
    assert records["session_reports"][0]["status"] == "INCOMPLETE"
    assert records["accounting"]["positions"] == {"NSE:INFY": 2}
    assert any(row["disposition"] == "EXPIRY_SETTLEMENT_REQUIRED"
               for row in records["session_reports"][0]["position_dispositions"])
    assert any(row["exposure_type"] == "STALE_MARK" for row in records["unresolved_exposures"])
    assert any(row["exposure_type"] == "MISSING_EXIT" for row in records["unresolved_exposures"])
    assert records["execution_receipts"][0]["status"] == "FILLED"
    shock_order_states = {row["paper_order_id"]: row["state"] for row in records["order_events"]
                          if row["paper_order_id"] in {"shock-stop", "shock-target"}}
    assert shock_order_states == {"shock-stop": "FILLED", "shock-target": "CANCELED"}
    runner.close()


def test_independent_daily_audit_verifies_raw_decision_and_quote_lineage(tmp_path):
    runner, fill_quote, decided = trade_setup(tmp_path)
    receipt = execute(runner, fill_quote, decided)
    assert receipt["status"] == "FILLED"
    runner.close()

    script = Path.cwd() / "scripts" / "forward_session_daily_audit.py"
    proof_dir = Path.cwd() / ".pytest-pa14-proof"
    proof_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(script), "--state-db", str(tmp_path / "state.sqlite"),
         "--journal-db", str(tmp_path / "journal.sqlite"), "--date", "2026-11-13",
         "--proof-dir", str(proof_dir), "--reference-journal-db", str(tmp_path / "journal.sqlite")],
        cwd=Path.cwd(), check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["session_count"] == 1
    assert report["decision_count"] == 1
    assert report["execution_receipt_count"] == 1
    [session] = report["sessions"]
    assert session["raw_hash_chain_valid"] is True
    assert session["decision_quote_lineage_valid"] is True
    assert session["raw_market_event_count"] == 2
    assert session["filled_receipt_count"] == 1
    result_report_comparison = report["replay_comparisons"][0]
    assert result_report_comparison["matches"] is True
    assert result_report_comparison["material_mismatch_count"] == 0
    proof = session["one_page_proof"]
    assert proof["generated_signal_count"] == 1
    assert proof["matched_fill_count"] == 1
    assert proof["missing_fill_count"] == 0
    assert proof["eligible_count"] is None
    assert proof["gross_pnl_inr"] is None
    assert proof["net_pnl_inr"] is None
    assert proof["performance_dimensions"]["by_strategy"]["momentum"] == {
        "decision_count": 1,
        "generated_signal_count": 1,
        "matched_fill_order_count": 1,
        "missing_fill_order_count": 0,
    }
    assert proof["performance_dimensions"]["by_asset_class"] is None
    assert proof["operational_checks"]["unaccounted_open_orders_zero"] is True
    assert proof["operational_checks"]["unsupported_fills_zero"] is True
    assert proof["operational_checks"]["unauthorized_broker_calls_zero"] is None
    assert proof["operational_checks"]["source_freshness_pass"] is True
    assert proof["operational_checks"]["complete_hashed_close_output"] is False
    proof_path = next(proof_dir.glob("session-*.md"))
    page = proof_path.read_text(encoding="utf-8")
    assert "Eligible strategy count | Unknown" in page
    assert "Matched fills | 1" in page
    assert "Benchmark comparison | Unknown" in page
    proof_path.unlink()
    proof_dir.rmdir()
    assert session["issues"] == []


@pytest.mark.parametrize("tamper,expected_flag", [
    ("chain", "raw_hash_chain_valid"),
    ("quote", "decision_quote_lineage_valid"),
])
def test_independent_daily_audit_fails_closed_on_raw_log_tampering(tmp_path, tamper, expected_flag):
    runner, fill_quote, decided = trade_setup(tmp_path)
    execute(runner, fill_quote, decided)
    runner.close()

    audit_copy = tmp_path / "tampered-journal.sqlite"
    shutil.copy2(tmp_path / "journal.sqlite", audit_copy)
    journal = sqlite3.connect(audit_copy)
    journal.execute("DROP TRIGGER IF EXISTS runner_audit_no_update")
    journal.execute("DROP TRIGGER IF EXISTS runner_audit_no_delete")
    if tamper == "chain":
        journal.execute("UPDATE runner_audit_log SET event_sha256=? WHERE kind='decision'",
                        ("f" * 64,))
    else:
        receipt_row = journal.execute(
            "SELECT sequence,record_json FROM runner_audit_log WHERE kind='execution_receipt'"
        ).fetchone()
        body = json.loads(receipt_row[1])
        body["quote_provenance"]["event_sha256"] = "e" * 64
        journal.execute("UPDATE runner_audit_log SET record_json=? WHERE sequence=?",
                        (json.dumps(body, sort_keys=True, separators=(",", ":")), receipt_row[0]))
    journal.commit()
    journal.close()

    script = Path.cwd() / "scripts" / "forward_session_daily_audit.py"
    result = subprocess.run(
        [sys.executable, str(script), "--state-db", str(tmp_path / "state.sqlite"),
         "--journal-db", str(audit_copy), "--date", "2026-11-13",
         "--reference-journal-db", str(tmp_path / "journal.sqlite")],
        cwd=Path.cwd(), check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "FAIL"
    [session] = report["sessions"]
    assert session[expected_flag] is False
    assert session["issues"]
    assert report["replay_comparisons"][0]["matches"] is False


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
    decision_payload = None
    if config.mode == "LIVE_PAPER":
        decision_payload = {"expected_value_forecast": {
            "candidate_id": "trade-1", "version": 1, "forecasted_at_utc": decided.isoformat(),
            "predicted_return_fraction": "0.01", "predicted_cost_fraction": "0.001",
            "expected_net_fraction": "0.009",
            "forecast": {"p10_fraction": "-0.01", "p50_fraction": "0.01",
                         "p90_fraction": "0.02", "probability_exceeds_cost": "0.6"},
            "model_sha256": "2" * 64,
            "cost_model_sha256": runner._resolved_cost_schedule("s").digest(),
        }}
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(signal_event.event_id,), payload=decision_payload)
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
    session_config = runner.state_store.config(session_id)
    if session_config.mode == "LIVE_PAPER":
        manifest.update({
            "strategy_sha256": session_config.strategy_sha256,
            "universe_sha256": hashlib.sha256(json.dumps(
                sorted(session_config.instrument_universe), separators=(",", ":")).encode()).hexdigest(),
            "risk_limits_sha256": hashlib.sha256(json.dumps(
                session_config.constraints["risk_limits"], sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "cost_model_sha256": runner._resolved_cost_schedule(session_id).digest(),
            "benchmark_id": session_config.benchmark,
            "capital_inr": format(session_config.capital, "f"),
        })
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
    intent = records["intents"][0]
    assert intent["as_of"] == signal_event.available_at.astimezone(timezone.utc).isoformat()
    assert intent["first_executable_after"] == (decided_at + timedelta(milliseconds=100)).isoformat()
    assert intent["expires_at"] == (decided_at + timedelta(milliseconds=5100)).isoformat()
    assert intent["last_good_quote_age_ms"] >= 0
    assert intent["execution_method"] == "MARKETABLE_LIMIT"
    assert timing["first_executable_after"] == intent["first_executable_after"]
    assert timing["expires_at"] == intent["expires_at"]
    assert timing["execution_method"] == ["MARKETABLE_LIMIT"]
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
    from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger
    from financial_brain.portfolio.durable_reservations import DurableReservationLedger

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
    canonical = CanonicalPortfolioLedger(tmp_path / "journal.sqlite").snapshot("ledger-1")
    assert canonical["positions"][0]["quantity"] == Decimal("2")
    assert canonical["cash"]["INR"] == Decimal("-200.40")
    reservations = DurableReservationLedger(tmp_path / "journal.sqlite")
    assert reservations.active_amounts("ledger-1") == {
        "CASH": Decimal("200.60"), "INSTRUMENT_QUANTITY:NSE:INFY": Decimal("2")}
    assert reservations.verify("ledger-1")["mismatched"] == 0
    assert runner._con.execute(
        "SELECT COUNT(*) FROM reservation_events WHERE event_kind='POSITION_FILL_RECORDED'"
    ).fetchone()[0] == 1
    runner.close()


def test_paper_intent_fails_closed_without_frozen_account_reservation_policy(tmp_path):
    config = make_config()
    constraints = dict(config.constraints)
    constraints.pop("reservation_policy")
    runner, event, decided = trade_setup(
        tmp_path, config=replace(config, constraints=constraints))

    with pytest.raises(ForwardSessionRunnerError, match="requires a frozen account reservation_policy"):
        execute(runner, event, decided)

    assert runner.records("s")["intents"] == []
    assert runner._con.execute("SELECT COUNT(*) FROM paper_intents").fetchone()[0] == 0
    assert runner._con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='reservations'"
    ).fetchone()[0] == 0
    runner.close()


def test_frozen_account_reservation_gates_paper_intent_and_retains_filled_exposure(tmp_path):
    from financial_brain.portfolio.durable_reservations import DurableReservationLedger

    policy = {"version": "paper-risk-v1", "frozen_at": (NOW - timedelta(seconds=1)).isoformat(),
              "source_sha256": "b" * 64,
              "limits": {"CASH": "100000", "INSTRUMENT_QUANTITY:NSE:INFY": "10"}}
    config = make_config_with_constraints({"reservation_policy": policy})
    runner, event, decided = trade_setup(tmp_path, config=config)
    receipt = execute(runner, event, decided)
    assert receipt["status"] == "FILLED"
    ledger = DurableReservationLedger(tmp_path / "journal.sqlite")
    assert ledger.active_amounts("ledger-1") == {
        "CASH": Decimal("200.60"), "INSTRUMENT_QUANTITY:NSE:INFY": Decimal("2")}
    assert ledger.verify("ledger-1")["mismatched"] == 0
    runner.close()


def test_futures_paper_fill_reserves_margin_and_risk_without_notional_cash(tmp_path):
    from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger
    from financial_brain.portfolio.durable_reservations import DurableReservationLedger

    config = make_config_with_constraints({
        "instrument_rules": fixture_rule_book("NSE:INFY", quantity_unit="FUTURES_CONTRACTS"),
        "position_accounting": {"NSE:INFY": {
            "asset_class": "FUTURE", "currency": "INR", "multiplier": "25"}},
        "reservation_resource_amounts": {"NSE:INFY": {"MARGIN": "100", "RISK_CAPITAL": "50"}},
    })
    runner, event, decided = trade_setup(tmp_path, config=config)

    receipt = execute(runner, event, decided, order_id="future-order")

    assert receipt["status"] == "FILLED"
    canonical = CanonicalPortfolioLedger(tmp_path / "journal.sqlite").snapshot("ledger-1")
    assert canonical["positions"][0]["asset_class"] == "FUTURE"
    assert canonical["positions"][0]["quantity"] == Decimal("2")
    assert canonical["positions"][0]["multiplier"] == Decimal("25")
    assert canonical["cash"] == {}
    active = DurableReservationLedger(tmp_path / "journal.sqlite").active_amounts("ledger-1")
    assert active["MARGIN"] == Decimal("200")
    assert active["RISK_CAPITAL"] == Decimal("100")
    assert "CASH" not in active
    runner.close()


def test_runner_persists_oco_lifecycle_and_blocks_close_until_sibling_cancel(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner, "oco-session")
    for order_id in ("stop", "target"):
        runner._append_paper_order_event(
            session_id="oco-session", order_id=order_id, state="CREATED", occurred_at=NOW,
            quantity=2, filled_quantity=0)
        runner._append_paper_order_event(
            session_id="oco-session", order_id=order_id, state="SUBMITTED", occurred_at=NOW,
            quantity=2, filled_quantity=0)
    armed = runner.arm_paper_oco(
        session_id="oco-session", group_id="position-1", stop_order_id="stop",
        target_order_id="target", occurred_at=NOW, evidence_id="a" * 64)
    assert armed.event == "ARMED"
    assert runner.arm_paper_oco(
        session_id="oco-session", group_id="position-1", stop_order_id="stop",
        target_order_id="target", occurred_at=NOW, evidence_id="a" * 64).event == "REPLAY"

    runner._append_paper_order_event(
        session_id="oco-session", order_id="stop", state="FILLED", occurred_at=NOW + timedelta(seconds=1),
        quantity=2, filled_quantity=2, price="99.00", fill_evidence_id="b" * 64)
    requested = runner.resolve_paper_oco_fill(
        session_id="oco-session", group_id="position-1", triggered_order_id="stop",
        fill_event_id=runner.order_journal.replay("oco-session::stop")[-1]["event_id"],
        occurred_at=NOW + timedelta(seconds=2), reconciliation_evidence_id="c" * 64)
    assert requested.event == "CANCEL_REQUESTED"
    with pytest.raises(ForwardSessionRunnerError, match="unresolved paper order states"):
        runner.close_session("oco-session", complete=True, now=NOW + timedelta(seconds=3))

    runner._append_paper_order_event(
        session_id="oco-session", order_id="target", state="CANCELED", occurred_at=NOW + timedelta(seconds=3),
        quantity=2, filled_quantity=0)
    confirmed = runner.resolve_paper_oco_fill(
        session_id="oco-session", group_id="position-1", triggered_order_id="stop",
        fill_event_id=runner.order_journal.replay("oco-session::stop")[-1]["event_id"],
        occurred_at=NOW + timedelta(seconds=4), reconciliation_evidence_id="c" * 64)
    assert confirmed.event == "CANCEL_CONFIRMED"
    assert len([row for row in runner.records("oco-session")["order_events"]
                if row["paper_order_id"] == "oco:position-1"]) == 5
    report = runner.close_session("oco-session", complete=True, now=NOW + timedelta(seconds=5))
    assert report["status"] == "CLOSED"
    runner.close()


def test_runner_paper_oco_late_sibling_fill_race_blocks_session_close(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner, "oco-race")
    for order_id in ("stop", "target"):
        runner._append_paper_order_event(
            session_id="oco-race", order_id=order_id, state="CREATED", occurred_at=NOW,
            quantity=2, filled_quantity=0)
        runner._append_paper_order_event(
            session_id="oco-race", order_id=order_id, state="SUBMITTED", occurred_at=NOW,
            quantity=2, filled_quantity=0)
    runner.arm_paper_oco(session_id="oco-race", group_id="position-1", stop_order_id="stop",
                         target_order_id="target", occurred_at=NOW, evidence_id="a" * 64)
    for order_id in ("stop", "target"):
        runner._append_paper_order_event(
            session_id="oco-race", order_id=order_id, state="FILLED",
            occurred_at=NOW + timedelta(seconds=1), quantity=2, filled_quantity=2,
            price="99.00", fill_evidence_id=("b" if order_id == "stop" else "d") * 64)
    result = runner.resolve_paper_oco_fill(
        session_id="oco-race", group_id="position-1", triggered_order_id="stop",
        fill_event_id=runner.order_journal.replay("oco-race::stop")[-1]["event_id"],
        occurred_at=NOW + timedelta(seconds=2), reconciliation_evidence_id="c" * 64)
    assert result.event == "SIBLING_FILL_RACE"
    with pytest.raises(ForwardSessionRunnerError, match="unresolved paper OCO groups"):
        runner.close_session("oco-race", complete=True, now=NOW + timedelta(seconds=3))
    runner.close()


def test_frozen_account_reservation_rejects_over_budget_without_paper_order(tmp_path):
    policy = {"version": "paper-risk-v1", "frozen_at": (NOW - timedelta(seconds=1)).isoformat(),
              "source_sha256": "b" * 64,
              "limits": {"CASH": "100", "INSTRUMENT_QUANTITY:NSE:INFY": "10"}}
    config = make_config_with_constraints({"reservation_policy": policy})
    runner, event, decided = trade_setup(tmp_path, config=config)
    with pytest.raises(ForwardSessionRunnerError, match="reservation denied.*budget exceeded"):
        execute(runner, event, decided)
    assert runner.records("s")["intents"] == []
    assert runner.records("s")["order_events"] == []
    runner.close()


def test_optional_reservation_inputs_cannot_override_computed_cash_exposure(tmp_path):
    policy = {"version": "paper-risk-v1", "frozen_at": (NOW - timedelta(seconds=1)).isoformat(),
              "source_sha256": "b" * 64,
              "limits": {"CASH": "100000", "INSTRUMENT_QUANTITY:NSE:INFY": "10"}}
    config = make_config_with_constraints({
        "reservation_policy": policy,
        "reservation_resource_amounts": {"NSE:INFY": {"CASH": "0.01"}},
    })
    runner, event, decided = trade_setup(tmp_path, config=config)
    with pytest.raises(ForwardSessionRunnerError, match="cannot override computed CASH"):
        execute(runner, event, decided)
    assert runner.records("s")["intents"] == []
    runner.close()


def test_index_level_profile_cannot_be_used_as_paper_execution_data(tmp_path):
    config = make_config_with_constraints({"data_profiles": [
        {"instrument_id": "NSE:INFY", "profile": "INDEX_LEVEL_ONLY"}
    ]})
    runner, event, decided = trade_setup(tmp_path, config=config)
    with pytest.raises(ForwardSessionRunnerError, match="index level-only profile cannot supply execution"):
        execute(runner, event, decided)
    assert runner.records("s")["intents"] == []
    runner.close()


def test_sourced_instrument_halt_blocks_fills_until_a_later_resumption(tmp_path):
    runner, quote, decided = trade_setup(tmp_path)
    halted_at = decided + timedelta(milliseconds=50)
    halt = MarketEvent(
        "instrument-halt", EventKind.HALT, "NSE", quote.instrument_id,
        halted_at, halted_at, halted_at, {"status": "HALTED"},
        "KITE_CONNECT_READ_ONLY", evidence_id="d" * 64,
    )
    runner.ingest("s", make_config(), [halt])
    with pytest.raises(ForwardSessionRunnerError, match="instrument is halted"):
        execute(runner, quote, decided)
    assert runner.records("s")["execution_receipts"] == []

    resumed_at = decided + timedelta(milliseconds=125)
    resumed = MarketEvent(
        "instrument-resumed", EventKind.HALT, "NSE", quote.instrument_id,
        resumed_at, resumed_at, resumed_at, {"status": "RESUMED"},
        "KITE_CONNECT_READ_ONLY", evidence_id="e" * 64,
    )
    runner.ingest("s", make_config(), [resumed])
    receipt = execute(runner, quote, decided, execution_offset_ms=150)
    assert receipt["status"] == "FILLED"
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
    assert receipt["quote_provenance"]["bid"] == "100.0"
    assert receipt["quote_provenance"]["ask"] == "100.1"
    assert receipt["quote_provenance"]["bid_quantity"] == 20
    assert receipt["quote_provenance"]["ask_quantity"] == 20
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
    assert runner.records("s")["operating_mode"] == "DEGRADED"
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
    assert runner.records("s")["operating_mode"] == "DEGRADED"
    assert runner.records("s")["intents"] == []
    assert runner.records("s")["execution_receipts"] == []
    runner.close()


def test_degraded_operating_mode_blocks_risk_increasing_paper_intents(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    runner.set_operating_mode(session_id="s", mode="DEGRADED", event_id="feed-degraded",
                              reason="quote feed freshness breached", occurred_at=decided)

    with pytest.raises(ForwardSessionRunnerError, match="blocked while operating mode is DEGRADED"):
        execute(runner, event, decided)

    assert runner.records("s")["operating_mode"] == "DEGRADED"
    assert runner.records("s")["intents"] == []
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
    from financial_brain.portfolio.durable_reservations import DurableReservationLedger
    active_reservations = DurableReservationLedger(tmp_path / "journal.sqlite").active_amounts("ledger-1")
    assert active_reservations["PAPER_COST_BUFFER"] >= Decimal(buy["charges_inr"])
    assert active_reservations["CASH"] >= Decimal("200.60") + Decimal(buy["charges_inr"])
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
    from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger
    canonical = CanonicalPortfolioLedger(tmp_path / "journal.sqlite").snapshot("ledger-1")
    assert canonical["cash"]["INR"] == Decimal(accounting["cash_inr"]) - Decimal("100000")
    assert sum(1 for row in CanonicalPortfolioLedger(tmp_path / "journal.sqlite").events("ledger-1")
               if row["event_kind"] == "FEE") == 2
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
    assert runner.paper_kill_switch.active
    assert runner.paper_kill_switch.active_reasons == ("DAILY_LOSS",)
    runner.close()


def test_live_paper_capture_requires_distinct_provider_permission(tmp_path):
    runner = make_runner(tmp_path)
    config = make_live_paper_config()
    opened(runner, config=config)
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
             "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                       "sell": [{"price": 100.05, "quantity": 3}]}}
    event = normalize_kite_quote("NSE:INFY", quote, received_at=NOW)
    with pytest.raises(ForwardSessionRunnerError, match="prospective_paper_capture.*blocked"):
        runner.ingest("s", config, [event])
    assert runner.records("s")["events"] == []
    runner.close()


def test_live_paper_capture_accepts_only_explicit_effective_scoped_permission(tmp_path):
    config = make_live_paper_config(include_prospective_rights=True)
    runner = make_runner(tmp_path)
    opened(runner, config=config)
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
             "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                       "sell": [{"price": 100.05, "quantity": 3}]}}
    event = normalize_kite_quote("NSE:INFY", quote, received_at=NOW)
    assert runner.ingest("s", config, [event])["persisted"] == 1
    runner.close()


def test_live_paper_manifest_binds_frozen_strategy_benchmark_capital_universe_cost_and_risk(tmp_path):
    config = make_live_paper_config(include_prospective_rights=True)
    runner, _fill_event, decided = trade_setup(tmp_path, config=config, include_manifest=False)
    decision = json.loads(runner._con.execute(
        "SELECT record_json FROM runner_decisions WHERE session_id='s' AND decision_id='trade-1'").fetchone()[0])
    event_json = runner._con.execute(
        "SELECT event_json FROM runner_events WHERE session_id='s' AND event_id=?",
        [decision["input_event_ids"][0]]).fetchone()[0]
    event = MarketEvent.from_dict(json.loads(event_json))
    manifest = record_manifest(runner, session_id="s", decision_id="trade-1", event=event,
                               decided_at=decided, quantity=11, persist=False)
    digest = runner.record_decision_manifest(session_id="s", decision_id="trade-1", manifest=manifest)
    assert len(digest) == 64
    audit_kinds = [row["kind"] for row in runner.durable.audit_records("s")]
    assert audit_kinds.index("expected_value_forecast") < audit_kinds.index("decision")
    decision_record = next(row for row in runner.records("s")["decisions"]
                           if row["decision_id"] == "trade-1")
    assert decision_record["payload"]["expected_value_forecast"]["expected_net_fraction"] == "0.009"
    expected_rows = runner.records("s")["expected_value_forecasts"]
    assert len(expected_rows) == 1 and expected_rows[0]["version"] == 1
    before_rebuild = runner.records("s")
    runner.rebuild_materialized_views("s")
    assert runner.records("s") == before_rebuild

    runner2, _fill_event2, decided2 = trade_setup(tmp_path / "mismatch", config=config, include_manifest=False)
    decision2 = json.loads(runner2._con.execute(
        "SELECT record_json FROM runner_decisions WHERE session_id='s' AND decision_id='trade-1'").fetchone()[0])
    event2_json = runner2._con.execute(
        "SELECT event_json FROM runner_events WHERE session_id='s' AND event_id=?",
        [decision2["input_event_ids"][0]]).fetchone()[0]
    event2 = MarketEvent.from_dict(json.loads(event2_json))
    manifest2 = record_manifest(runner2, session_id="s", decision_id="trade-1", event=event2,
                                decided_at=decided2, quantity=11, persist=False)
    manifest2["benchmark_id"] = "OTHER"
    with pytest.raises(ForwardSessionRunnerError, match="benchmark differs"):
        runner2.record_decision_manifest(session_id="s", decision_id="trade-1", manifest=manifest2)
    runner.close()
    runner2.close()


def test_live_paper_candidate_requires_predecision_expected_value_forecast(tmp_path):
    runner = make_runner(tmp_path)
    config = make_live_paper_config(include_prospective_rights=True)
    opened(runner, config=config)
    event = make_event(event_id="missing-live-forecast")
    runner.ingest("s", config, [event])
    with pytest.raises(ForwardSessionRunnerError, match="pre-entry expected-value forecast"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="missing-forecast",
                      outcome="TRADE", input_event_ids=(event.event_id,), decided_at=NOW + timedelta(seconds=1))
    assert runner.records("s")["decisions"] == []
    assert not any(row["kind"] == "expected_value_forecast" for row in runner.durable.audit_records("s"))
    runner.close()


def test_expected_value_revisions_are_sequential_new_and_immutable(tmp_path):
    runner, event, first_decision_at = trade_setup(
        tmp_path, config=make_live_paper_config(include_prospective_rights=True))
    cost_sha = runner._resolved_cost_schedule("s").digest()

    def forecast(version, forecast_at):
        return {"candidate_id": "trade-1", "version": version,
                "forecasted_at_utc": forecast_at.isoformat(),
                "predicted_return_fraction": "0.012", "predicted_cost_fraction": "0.001",
                "expected_net_fraction": "0.011",
                "forecast": {"p10_fraction": "-0.02", "p50_fraction": "0.012",
                             "p90_fraction": "0.03", "probability_exceeds_cost": "0.7"},
                "model_sha256": "2" * 64, "cost_model_sha256": cost_sha}

    revised_at = first_decision_at + timedelta(seconds=1)
    assert runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-2",
                         outcome="NO_TRADE", input_event_ids=(event.event_id,), decided_at=revised_at,
                         reason_codes=("REVISED_FORECAST_NO_ENTRY",),
                         payload={"expected_value_forecast": forecast(2, revised_at - timedelta(milliseconds=1))})
    with pytest.raises(ForwardSessionRunnerError, match="next immutable version"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-4",
                      outcome="NO_TRADE", input_event_ids=(event.event_id,),
                      decided_at=revised_at + timedelta(milliseconds=10),
                      reason_codes=("VERSION_GAP",),
                      payload={"expected_value_forecast": forecast(4, revised_at)})
    with pytest.raises(ForwardSessionRunnerError, match="published before its decision clock"):
        runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-future-forecast",
                      outcome="NO_TRADE", input_event_ids=(event.event_id,),
                      decided_at=revised_at + timedelta(milliseconds=20),
                      reason_codes=("FUTURE_FORECAST",),
                      payload={"expected_value_forecast": forecast(
                          3, revised_at + timedelta(milliseconds=21))})
    decisions = runner.records("s")["decisions"]
    assert [row["payload"].get("expected_value_forecast", {}).get("version") for row in decisions] == [1, 2]
    runner.close()


def test_live_paper_manifest_cannot_revise_published_expected_value(tmp_path):
    config = make_live_paper_config(include_prospective_rights=True)
    runner, _fill_event, decided = trade_setup(tmp_path, config=config, include_manifest=False)
    decision = runner.records("s")["decisions"][0]
    event, _digest = runner.source_event("s", decision["input_event_ids"][0])
    manifest = record_manifest(runner, session_id="s", decision_id="trade-1", event=event,
                               decided_at=decided, quantity=11, persist=False)
    manifest["predicted_return_fraction"] = "0.02"
    with pytest.raises(ForwardSessionRunnerError, match="forecast differs from the frozen decision"):
        runner.record_decision_manifest(session_id="s", decision_id="trade-1", manifest=manifest)
    runner.close()


def test_live_paper_execution_clock_must_follow_expected_value_publication(tmp_path):
    runner, event, decided = trade_setup(
        tmp_path, config=make_live_paper_config(include_prospective_rights=True))
    with pytest.raises(ForwardSessionRunnerError, match="must precede the execution clock"):
        execute(runner, event, decided, execution_offset_ms=0)
    assert runner.records("s")["intents"] == []
    runner.close()


def test_live_paper_close_cannot_seal_open_inventory_without_risk_policy(tmp_path):
    config = make_live_paper_config(include_prospective_rights=True)
    config = replace(config, constraints={**config.constraints, "liquidity_bucket": "large"})
    runner, event, decided = trade_setup(
        tmp_path, config=config)
    execute(runner, event, decided)
    with pytest.raises(ForwardSessionRunnerError, match="OPEN_RISK_MISSING_OR_AMBIGUOUS"):
        runner.close_session("s", complete=True, now=decided + timedelta(seconds=2))

    report = runner.close_session("s", complete=False, now=decided + timedelta(seconds=2),
                                  reason="position risk policy is missing")
    reconciliation = report["day_end_reconciliation"]
    assert reconciliation["fees_reconciled"] is True
    assert reconciliation["inventory_reconciled"] is True
    assert reconciliation["marked_equity_inr"] is not None
    assert reconciliation["open_risk_inr"] is None
    assert reconciliation["reconciled"] is False
    exposures = report["unresolved_exposures"]
    assert len(exposures) == 1
    assert exposures[0]["exposure_type"] == "OPEN_RISK_UNKNOWN"
    assert exposures[0]["loss_inr"] is None
    assert exposures[0]["pnl_status"] == "UNRECOGNIZED_PENDING_EVIDENCE"
    runner.close()


def test_unresolved_exposure_requires_persisted_evidence_to_resolve(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    exposure_id = runner.record_unresolved_exposure(
        session_id="s", exposure_type="NO_CONFIRMABLE_QUOTE", exposure_key="mark:NSE:INFY",
        instrument_id="NSE:INFY", quantity=2,
        reason="exit mark unavailable during close", occurred_at=decided)
    assert runner.record_unresolved_exposure(
        session_id="s", exposure_type="NO_CONFIRMABLE_QUOTE", exposure_key="mark:NSE:INFY",
        instrument_id="NSE:INFY", quantity=2,
        reason="exit mark unavailable during close", occurred_at=decided) == exposure_id
    assert runner.records("s")["unresolved_exposures"][0]["loss_inr"] is None
    with pytest.raises(ForwardSessionRunnerError, match="not persisted"):
        runner.resolve_unresolved_exposure(
            session_id="s", exposure_id=exposure_id, evidence_event_id="missing-event",
            resolution="FRESH_MARK", reason="attempt without evidence", resolved_at=decided)
    runner.resolve_unresolved_exposure(
        session_id="s", exposure_id=exposure_id, evidence_event_id=event.event_id,
        resolution="FRESH_MARK", reason="journaled quote reviewed", resolved_at=decided + timedelta(seconds=1))
    assert runner.records("s")["unresolved_exposures"] == []
    assert any(row["kind"] == "unresolved_exposure_resolution"
               for row in runner.records("s")["audit_log"])
    runner.close()


def test_stale_position_mark_is_journaled_without_zero_loss_substitution(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    execute(runner, event, decided)
    report = runner.close_session("s", complete=True, now=decided + timedelta(seconds=10))
    reconciliation = report["day_end_reconciliation"]
    assert reconciliation["marked_equity_inr"] is None
    [exposure] = [row for row in report["unresolved_exposures"]
                  if row["exposure_type"] == "STALE_MARK"]
    assert exposure["valuation_inr"] is None
    assert exposure["loss_inr"] is None
    assert exposure["pnl_status"] == "UNRECOGNIZED_PENDING_EVIDENCE"
    assert "POSITION_MARK_STALE:NSE:INFY" in reconciliation["exceptions"]
    runner.close()


def test_live_paper_flat_close_reconciles_cash_costs_equity_and_zero_open_risk(tmp_path):
    runner = make_runner(tmp_path)
    config = make_live_paper_config(include_prospective_rights=True)
    opened(runner, config=config)
    report = runner.close_session("s", complete=True, now=NOW + timedelta(seconds=2))
    reconciliation = report["day_end_reconciliation"]
    assert reconciliation["reconciled"] is True
    assert reconciliation["oms_reconciled"] is True
    assert reconciliation["cash_reconciled"] is True
    assert reconciliation["fees_reconciled"] is True
    assert reconciliation["inventory_reconciled"] is True
    assert reconciliation["marked_equity_inr"] == "100000"
    assert reconciliation["open_risk_inr"] == "0"
    runner.close()


def test_live_paper_session_rejects_missing_frozen_parameters_before_creation(tmp_path):
    from dataclasses import replace

    runner = make_runner(tmp_path)
    with pytest.raises(ForwardSessionRunnerError, match="frozen strategy_parameters"):
        opened(runner, config=replace(make_config(), mode="LIVE_PAPER"))
    with pytest.raises(SessionStateError, match="unknown forward session"):
        runner.state_store.state("s")
    runner.close()


def test_risk_breach_latch_persists_across_runner_restart_and_blocks_entries(tmp_path):
    runner, _event, _decided = trade_setup(tmp_path, risk_allowed=False)
    assert runner.paper_kill_switch.active
    runner.close()

    resumed = make_runner(tmp_path)
    assert resumed.paper_kill_switch.active
    with pytest.raises(ForwardSessionRunnerError, match="paper kill switch is active: DAILY_LOSS"):
        resumed._require_paper_entries_enabled()
    resumed.close()


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
    audit = baseline["audit_log"]
    assert all(row["event_sha256"] and row["previous_event_sha256"] for row in audit)
    market_record = next(row["record"] for row in audit if row["kind"] == "market_event")
    assert market_record["event"]["exchange_at"]
    assert market_record["event"]["received_at"]
    assert market_record["event"]["available_at"]
    assert market_record["ingested_at"] and market_record["ingest_clock_source"]
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


def test_audit_hash_chain_detects_tampered_record_and_previous_link(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    execute(runner, event, decided)
    rows = runner.durable.audit_records("s")
    assert len(rows) >= 4
    assert rows[0]["previous_event_sha256"] == "0" * 64
    assert rows[1]["previous_event_sha256"] == rows[0]["event_sha256"]
    runner._con.execute("DROP TRIGGER runner_audit_no_update")
    runner._con.execute("UPDATE runner_audit_log SET record_json='{}' WHERE session_id='s' AND sequence=2")
    with pytest.raises(DurableJournalError, match="hash chain verification failed"):
        runner.durable.audit_records("s")
    runner.close()


def test_audit_hash_chain_backfills_legacy_rows_on_reopen(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    execute(runner, event, decided)
    runner._con.execute("DROP TRIGGER runner_audit_no_update")
    runner._con.execute("ALTER TABLE runner_audit_log DROP COLUMN previous_event_sha256")
    runner._con.execute("ALTER TABLE runner_audit_log DROP COLUMN event_sha256")
    runner.close()

    resumed = make_runner(tmp_path)
    rows = resumed.durable.audit_records("s")
    assert rows and rows[0]["previous_event_sha256"] == "0" * 64
    assert all(row["event_sha256"] for row in rows)
    resumed.close()


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
    close_report = runner.close_session("s", complete=True, now=first_close_at)
    assert close_report["position_dispositions"] == [{
        "instrument_id": "NSE:INFY", "quantity": 2, "quantity_unit": "SHARES", "expiry": None,
        "disposition": "OVERNIGHT_CARRY_AUTHORIZED", "valuation_status": "OBSERVED_EXECUTABLE_QUOTE",
        "mark_price_inr": "99.95", "mark_quote_event_id": buy_quote.event_id,
        "execution_or_settlement_fill_created": False,
    }]
    assert close_report["day_end_reconciliation"]["inventory_reconciled"] is True
    assert close_report["day_end_reconciliation"]["marked_equity_inr"] is not None
    assert close_report["day_end_reconciliation"]["open_risk_inr"] is None
    assert "OPEN_RISK_MISSING_OR_AMBIGUOUS:NSE:INFY" in close_report[
        "day_end_reconciliation"]["exceptions"]
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


def test_complete_close_requires_explicit_carry_authorization_for_open_inventory(tmp_path):
    config = make_config()
    config = replace(config, constraints={**config.constraints, "carry_authorized_instruments": []})
    runner, event, decided = trade_setup(tmp_path, config=config)
    receipt = execute(runner, event, decided, order_id="open-position", qty=2)
    assert receipt["filled_quantity"] == 2

    with pytest.raises(ForwardSessionRunnerError, match="positions requiring flatten or expiry action"):
        runner.close_session("s", complete=True, now=decided + timedelta(seconds=2))
    report = runner.close_session("s", complete=False, now=decided + timedelta(seconds=2),
                                  reason="position not flattened")
    assert report["position_dispositions"][0]["disposition"] == "INTRADAY_FLATTEN_REQUIRED"
    assert report["position_dispositions"][0]["execution_or_settlement_fill_created"] is False
    runner.close()


def test_expiring_option_position_is_reported_separately_and_blocks_complete_close(tmp_path):
    config = make_config()
    rules = fixture_rule_book("NSE:INFY", quantity_unit="OPTION_CONTRACTS",
                              expiry=config.exchange_date)
    config = replace(config, constraints={**config.constraints, "instrument_rules": rules,
                                          "carry_authorized_instruments": ["NSE:INFY"],
                                          "position_accounting": {"NSE:INFY": {
                                              "asset_class": "OPTION", "currency": "INR",
                                              "multiplier": "1"}}})
    runner, event, decided = trade_setup(tmp_path, config=config)
    receipt = execute(runner, event, decided, order_id="expiring-option", qty=2)
    assert receipt["filled_quantity"] == 2

    with pytest.raises(ForwardSessionRunnerError, match="positions requiring flatten or expiry action"):
        runner.close_session("s", complete=True, now=decided + timedelta(seconds=2))
    report = runner.close_session("s", complete=False, now=decided + timedelta(seconds=2),
                                  reason="expiry settlement evidence unavailable")
    position = report["position_dispositions"][0]
    assert position["quantity_unit"] == "OPTION_CONTRACTS"
    assert position["disposition"] == "EXPIRY_SETTLEMENT_REQUIRED"
    assert position["valuation_status"] == "OBSERVED_EXECUTABLE_QUOTE"
    assert position["execution_or_settlement_fill_created"] is False
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
    monitor_rows = [row for row in runner.durable.audit_records("s")
                    if row["kind"] == "position_monitor"]
    assert len(monitor_rows) == 1
    assert monitor_rows[0]["record"]["evaluation"] == "HOLD"
    assert monitor_rows[0]["record"]["quote_event_id"] == entry_quote.event_id
    assert monitor_rows[0]["record"]["quote_event_sha256"] == entry_quote.sha256()
    assert monitor_rows[0]["record"]["bid"] == 99.95
    assert monitor_rows[0]["record"]["ask"] == 100.05
    assert monitor_rows[0]["record"]["available_at"] == entry_quote.available_at.isoformat()
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
    monitor_rows = [row for row in runner.durable.audit_records("s2")
                    if row["kind"] == "position_monitor"]
    assert [row["record"]["evaluation"] for row in monitor_rows] == ["STOP", "TIME"]
    assert all(row["record"]["quote_event_sha256"] == stop_quote.sha256() for row in monitor_rows)
    assert all(row["record"]["bid"] == 94.95 and row["record"]["ask"] == 95.05
               for row in monitor_rows)
    assert all(row["record"]["available_at"] == stop_quote.available_at.isoformat()
               for row in monitor_rows)
    assert due_exits(book, runner=runner, session_id="s2", mode="live", version=version,
                     exchange_date=exit_day,
                     completed_sessions_held={signal.signal_id: signal.horizon_sessions},
                     instrument_ids={"INFY": "NSE:INFY"},
                     quote_events={"NSE:INFY": stop_quote}) == time_exits
    replayed_monitor_rows = [row for row in runner.durable.audit_records("s2")
                             if row["kind"] == "position_monitor"]
    assert len(replayed_monitor_rows) == 2
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
