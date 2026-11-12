from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _config(**changes):
    values = dict(session_id="S-1", exchange_date="2026-11-12", cohort_id="C1",
                  strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
                  constraints_sha256="c" * 64, ledger_id="LEDGER-1",
                  benchmark_id="NIFTY50", instruments=("NSE:INFY",),
                  capital_inr="1000000", mode=Mode.LIVE_PAPER)
    values.update(changes)
    return ForwardRunConfig(**values)


def _event(available=NOW - timedelta(seconds=1)):
    return NormalizedEvent("QUOTE", "NSE:INFY", available - timedelta(milliseconds=5),
                           available - timedelta(milliseconds=2), available,
                           (("last_price", "1500"),))


def test_runner_persists_visible_events_decision_risk_and_paper_execution(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    calls = []
    result = runner.run(_config(), [_event(), _event(NOW + timedelta(seconds=1))], decision_at=NOW,
                        decision_fn=lambda events: {"side": "BUY", "count": len(events)},
                        risk_fn=lambda decision: {"approved": True},
                        execution_fn=lambda decision, intent, events: calls.append((intent, events)) or {"state": "NO_TRADE"})
    assert result["status"] == "PAPER_COMPLETE"
    assert result["event_count"] == 2
    assert result["decision_event_count"] == 1
    assert result["physical_orders"] is False
    assert len(calls) == 1 and len(calls[0][1]) == 2
    record_types = [r["t"] for r in DurableJournal(tmp_path / "session.jsonl").records()]
    assert record_types == ["SESSION_PLAN", "SESSION_STATE", "SESSION_STATE", "SESSION_STATE",
                            "MARKET_EVENT", "MARKET_EVENT", "DECISION", "RISK_VERDICT", "SESSION_STATE",
                            "OUTBOX_PENDING", "PAPER_EXECUTION", "OUTBOX_ACK", "SESSION_STATE", "SESSION_CLOSED"]
    states = [r["state"] for r in DurableJournal(tmp_path / "session.jsonl").records()
              if r["t"] == "SESSION_STATE"]
    assert states == ["PLANNED", "PREFLIGHT", "OPEN", "DRAINING", "CLOSED"]


def test_closed_session_is_replayed_without_reexecuting(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    calls = []
    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "NONE"},
                  risk_fn=lambda _: {"approved": True},
                  execution_fn=lambda _, intent, events: calls.append(intent) or {"state": "NO_TRADE"})
    first = runner.run(_config(), [_event()], **kwargs)
    second = runner.run(_config(), [_event()], **kwargs)
    assert first == second
    assert first["status"] == "NO_TRADE" and first["reason"] == "NO_SIGNAL"
    assert len(calls) == 0


def test_no_trade_signal_never_reaches_execution_adapter(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "no-trade.jsonl")
    calls = []
    result = runner.run(_config(), [_event()], decision_at=NOW,
                        decision_fn=lambda _: {"side": "NONE", "reason": "EDGE_BELOW_COST"},
                        risk_fn=lambda _: {"approved": True},
                        execution_fn=lambda *args: calls.append(args) or {"state": "FILLED"})
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "EDGE_BELOW_COST"
    assert calls == []


def test_run_config_hash_normalizes_capital_and_rejects_duplicate_instruments():
    assert _config().sha256 == "99aae10b3c9a27e3c34bddaa20134655d50f2611a17bfcf8ecaebd59eb185677"
    assert _config(capital_inr="1000000.00").sha256 == _config(capital_inr="1000000").sha256
    assert _config(ledger_id="LEDGER-2").sha256 != _config().sha256
    assert _config(risk_policy_sha256="d" * 64).sha256 != _config().sha256
    with pytest.raises(RunnerError, match="unique"):
        _config(instruments=("NSE:INFY", "NSE:INFY")).canonical()


def test_pending_outbox_retries_with_same_idempotency_key_after_restart(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    executions = {}
    attempts = []

    def execution(_decision, intent_id, _events):
        attempts.append(intent_id)
        if intent_id not in executions:
            executions[intent_id] = {"state": "FILLED", "fill_id": "F-1"}
            if len(attempts) == 1:
                raise RuntimeError("simulated process loss after paper side effect")
        return executions[intent_id]

    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "BUY"},
                  risk_fn=lambda _: {"approved": True}, execution_fn=execution)
    with pytest.raises(RuntimeError, match="process loss"):
        runner.run(_config(), [_event()], **kwargs)
    recovered = runner.run(_config(), [_event()], **kwargs)
    assert recovered["execution"] == {"state": "FILLED", "fill_id": "F-1"}
    assert attempts[0] == attempts[1]
    records = DurableJournal(journal_path).records()
    assert [r["t"] for r in records].count("PAPER_EXECUTION") == 1
    assert [r["t"] for r in records].count("OUTBOX_ACK") == 1


def test_session_id_cannot_be_reused_for_different_configuration(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    runner.run(_config(), [], decision_at=NOW, decision_fn=lambda _: {"side": "NONE"},
                   risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})
    with pytest.raises(RunnerError, match="different"):
        runner.run(_config(cohort_id="C2"), [], decision_at=NOW,
                   decision_fn=lambda _: {}, risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})


def test_runner_rejects_physical_or_naive_clock_modes(tmp_path):
    with pytest.raises(RunnerError, match="only accepts"):
        _config(mode=Mode.LIVE_CASH).canonical()
    journal_path = tmp_path / "j"
    with pytest.raises(RunnerError, match="timezone-aware"):
        ForwardSessionRunner(journal_path).run(
            _config(), [], decision_at=datetime(2026, 11, 12), decision_fn=lambda _: {},
            risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})
    assert DurableJournal(journal_path).records() == []


def test_non_boolean_risk_approval_is_rejected_without_execution(tmp_path):
    journal_path = tmp_path / "risk.jsonl"
    calls = []
    with pytest.raises(RunnerError, match="boolean approved"):
        ForwardSessionRunner(journal_path).run(
            _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY"},
            risk_fn=lambda _: {"approved": "false"}, execution_fn=lambda *args: calls.append(args) or {})
    assert calls == []
    assert all(r["t"] != "RISK_VERDICT" for r in DurableJournal(journal_path).records())


def test_risk_rejection_is_terminal_and_never_executes(tmp_path):
    journal_path = tmp_path / "rejected.jsonl"
    calls = []
    result = ForwardSessionRunner(journal_path).run(
        _config(), [_event()], decision_at=NOW, decision_fn=lambda _: {"side": "BUY"},
        risk_fn=lambda _: {"approved": False, "reason": "MAX_EXPOSURE"},
        execution_fn=lambda *args: calls.append(args) or {"state": "FILLED"})
    assert result["status"] == "REJECTED" and result["reason"] == "MAX_EXPOSURE"
    assert calls == []
    states = [r["state"] for r in DurableJournal(journal_path).records() if r["t"] == "SESSION_STATE"]
    assert states[-1] == "REJECTED"


def test_session_restart_rejects_changed_decision_timestamp(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "clock.jsonl")
    kwargs = dict(decision_fn=lambda _: {"side": "NONE"}, risk_fn=lambda _: {"approved": True},
                  execution_fn=lambda *_: {})
    runner.run(_config(), [_event()], decision_at=NOW, **kwargs)
    with pytest.raises(RunnerError, match="different decision timestamp"):
        runner.run(_config(), [_event()], decision_at=NOW + timedelta(seconds=1), **kwargs)


def test_incomplete_session_is_preserved_and_cannot_be_reused(tmp_path):
    journal_path = tmp_path / "incomplete.jsonl"
    runner = ForwardSessionRunner(journal_path)
    with pytest.raises(RuntimeError, match="strategy outage"):
        runner.run(_config(), [_event()], decision_at=NOW,
                   decision_fn=lambda _: (_ for _ in ()).throw(RuntimeError("strategy outage")),
                   risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {})
    runner.mark_incomplete("S-1", "strategy outage")
    records = DurableJournal(journal_path).records()
    assert [r["state"] for r in records if r["t"] == "SESSION_STATE"][-1] == "INCOMPLETE"
    with pytest.raises(RunnerError, match="terminal"):
        runner.run(_config(), [_event()], decision_at=NOW,
                   decision_fn=lambda _: {"side": "NONE"}, risk_fn=lambda _: {"approved": True},
                   execution_fn=lambda *_: {})


def test_invalid_state_transition_fails_closed(tmp_path):
    journal = DurableJournal(tmp_path / "state.jsonl")
    journal.append({"t": "SESSION_PLAN", "session_id": "S-1"})
    ForwardSessionRunner._ensure_state(journal, "S-1", "PLANNED")
    with pytest.raises(RunnerError, match="invalid session transition"):
        ForwardSessionRunner._ensure_state(journal, "S-1", "OPEN")
