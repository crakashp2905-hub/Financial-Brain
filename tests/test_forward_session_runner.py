from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _config(**changes):
    values = dict(session_id="S-1", exchange_date="2026-11-12", cohort_id="C1",
                  strategy_sha256="a" * 64, benchmark_id="NIFTY50", instruments=("NSE:INFY",),
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
                        execution_fn=lambda decision, intent: calls.append(intent) or {"state": "NO_TRADE"})
    assert result["status"] == "PAPER_COMPLETE"
    assert result["event_count"] == 1
    assert result["physical_orders"] is False
    assert len(calls) == 1
    record_types = [r["t"] for r in DurableJournal(tmp_path / "session.jsonl").records()]
    assert record_types == ["SESSION_PLAN", "MARKET_EVENT", "DECISION", "RISK_VERDICT",
                            "OUTBOX_PENDING", "PAPER_EXECUTION", "OUTBOX_ACK", "SESSION_CLOSED"]


def test_closed_session_is_replayed_without_reexecuting(tmp_path):
    journal_path = tmp_path / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    calls = []
    kwargs = dict(decision_at=NOW, decision_fn=lambda _: {"side": "NONE"},
                  risk_fn=lambda _: {"approved": True},
                  execution_fn=lambda _, intent: calls.append(intent) or {"state": "NO_TRADE"})
    first = runner.run(_config(), [_event()], **kwargs)
    second = runner.run(_config(), [_event()], **kwargs)
    assert first == second
    assert len(calls) == 1


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
    with pytest.raises(RunnerError, match="timezone-aware"):
        ForwardSessionRunner(tmp_path / "j").run(
            _config(), [], decision_at=datetime(2026, 11, 12), decision_fn=lambda _: {},
            risk_fn=lambda _: {"approved": False}, execution_fn=lambda *_: {})
