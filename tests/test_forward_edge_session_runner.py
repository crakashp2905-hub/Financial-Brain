from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
import sqlite3

import pytest

from financial_brain.forward_edge.market_data import normalize_kite_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_runner import (
    ForwardSessionRunner,
    ForwardSessionRunnerError,
)
from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
)


NOW = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)


def make_config():
    return ForwardRunConfig(date(2026, 11, 13), "cohort-v1", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"), {"max_positions": 1}, "ledger-1")


def make_event(*, event_id="fixture-1", available_at=NOW, event_time=NOW):
    return MarketEvent(event_id, EventKind.QUOTE, "NSE", "NSE:INFY", event_time,
                       available_at, available_at, {"bid": 100.0, "ask": 100.1}, "FIXTURE")


def make_runner(tmp_path):
    return ForwardSessionRunner(ForwardSessionStore(tmp_path / "state.sqlite"), tmp_path / "journal.sqlite")


def opened(runner, session_id="s"):
    return runner.begin(session_id, make_config(), now=NOW,
                        preflight={"data_rights_configured": True, "risk_policy_loaded": True})


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
    state = runner.begin("s", make_config(), now=NOW, preflight={"feed_entitlement": False})
    assert state is SessionState.REJECTED
    with pytest.raises(ForwardSessionRunnerError, match="OPEN session"):
        runner.ingest("s", make_config(), [make_event()])
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
    runner.ingest("s", make_config(), [make_event(available_at=available)])
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


def trade_setup(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
             "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                       "sell": [{"price": 100.05, "quantity": 3}]}}
    event = normalize_kite_quote("NSE:INFY", quote, received_at=NOW)
    runner.ingest("s", make_config(), [event])
    decided = NOW + timedelta(seconds=1)
    runner.decide(session_id="s", strategy_id="momentum", decision_id="trade-1", outcome="TRADE",
                  decided_at=decided, input_event_ids=(event.event_id,))
    return runner, event, decided


def execute(runner, event, decided, *, order_id="order-1", qty=2, limit="100.30"):
    return runner.execute_paper_intent(
        session_id="s", order_id=order_id, decision_id="trade-1", event_id=event.event_id,
        side="BUY", quantity=qty, limit_price=Decimal(limit), tick_size=Decimal("0.05"),
        freeze_limit=10, slice_size=10, adverse_slippage_bps=Decimal("10"), latency_ms=100,
        simulated_at=decided + timedelta(milliseconds=100))


def test_durable_outbox_persists_only_causally_eligible_paper_fill_once(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    first = execute(runner, event, decided)
    second = execute(runner, event, decided)
    assert first == second
    assert first["status"] == "FILLED" and first["filled_quantity"] == 2
    assert first["average_price"] == "100.20"
    assert first["physical_order_submitted"] is False
    assert runner.records("s")["execution_receipts"] == [first]
    assert runner._con.execute("SELECT COUNT(*) FROM paper_execution_receipts").fetchone()[0] == 1
    runner.close()


def test_partial_missed_and_rejected_scenarios_keep_requested_vs_filled_separate(tmp_path):
    runner, event, decided = trade_setup(tmp_path)
    partial = execute(runner, event, decided, order_id="partial", qty=5)
    assert partial["status"] == "PARTIAL" and partial["filled_quantity"] == 3
    missed = execute(runner, event, decided, order_id="missed", qty=2, limit="100.00")
    assert missed["status"] == "MISSED" and missed["filled_quantity"] == 0
    rejected = execute(runner, event, decided, order_id="freeze", qty=11)
    assert rejected["status"] == "REJECTED" and rejected["filled_quantity"] == 0
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
                        preflight={"risk_policy_loaded": True}) is SessionState.OPEN
    event = normalize_kite_quote("NSE:INFY", {
        "timestamp": "2026-11-13 10:15:00", "last_price": 100.0, "volume": 1000,
        "depth": {"buy": [{"price": 99.95, "quantity": 20}],
                  "sell": [{"price": 100.05, "quantity": 3}]},
    }, received_at=NOW)
    runner.ingest("clock-session", make_config(), [event],
                  ingest_metadata={event.event_id: (NOW + timedelta(milliseconds=10), "FIXTURE_CLOCK")})
    runner.decide(session_id="clock-session", strategy_id="momentum", decision_id="clock-trade",
                  outcome="TRADE", input_event_ids=(event.event_id,))
    receipt = runner.execute_paper_intent(
        session_id="clock-session", order_id="clock-order", decision_id="clock-trade",
        event_id=event.event_id, side="BUY", quantity=2, limit_price=Decimal("100.30"),
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

    def crash_after_persist(session_id, order_id, body):
        persist(session_id, order_id, body)
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
    receipt = execute(runner, event, decided)
    assert receipt["status"] == "FILLED"
    assert runner._con.execute("SELECT COUNT(*) FROM paper_execution_receipts").fetchone()[0] == 1
    report = runner.close_session("s", now=decided + timedelta(seconds=1), complete=True)
    assert report["status"] == "CLOSED" and report["execution_receipt_count"] == 1
    runner.close()
