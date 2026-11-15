from datetime import date, datetime, timezone
from decimal import Decimal

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
    assert runner.ingest_kite_archive("kite-session", make_config(), archive)["persisted"] == 1
    archive.close()
    records = runner.records("kite-session")
    assert records["events"][0]["source"] == "KITE_CONNECT_READ_ONLY"
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


def test_incomplete_close_is_explicit_and_idempotent(tmp_path):
    runner = make_runner(tmp_path)
    opened(runner)
    report = runner.close_session("s", now=NOW, complete=False, reason="quote stream ended early")
    assert report["status"] == "INCOMPLETE"
    assert runner.close_session("s", now=NOW, complete=False, reason="different retry detail") == report
    assert runner.state_store.state("s") is SessionState.INCOMPLETE
    runner.close()
