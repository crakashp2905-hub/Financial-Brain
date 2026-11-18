from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
BOUNDARIES = (
    "SESSION_PLAN", "SESSION_STATE", "PREFLIGHT_RESULT", "MARKET_EVENT", "DECISION",
    "RISK_VERDICT", "PAPER_INTENT", "OUTBOX_PENDING", "PAPER_EXECUTION", "OUTBOX_ACK",
    "SESSION_CLOSED",
)


def _config():
    return ForwardRunConfig(
        session_id="crash-matrix", exchange_date="2026-11-12", cohort_id="C1",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="LEDGER-1", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr="1000000", mode=Mode.LIVE_PAPER,
    )


def _event():
    available = NOW - timedelta(seconds=1)
    return NormalizedEvent("QUOTE", "NSE:INFY", available - timedelta(milliseconds=5),
                           available - timedelta(milliseconds=2), available,
                           (("bid_price", "99.99"), ("ask_price", "100.00"),
                            ("bid_qty", "10"), ("ask_qty", "10")))


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_lifecycle_crash_at_each_durable_boundary_recovers_one_economic_event(
    tmp_path, monkeypatch, boundary
):
    path = tmp_path / f"{boundary.lower()}.jsonl"
    runner = ForwardSessionRunner(path)
    original_append = DurableJournal.append
    original_append_many = DurableJournal.append_many
    crashed = False

    def crash_after_append(journal, record):
        nonlocal crashed
        sequence = original_append(journal, record)
        if not crashed and record.get("t") == boundary:
            crashed = True
            raise RuntimeError(f"crash after {boundary}")
        return sequence

    def crash_after_batch(journal, records):
        nonlocal crashed
        sequences = original_append_many(journal, records)
        if not crashed and boundary == "MARKET_EVENT" and any(r.get("t") == boundary for r in records):
            crashed = True
            raise RuntimeError("crash after MARKET_EVENT")
        return sequences

    execution_calls = []

    def execution(_decision, intent_id, _events):
        execution_calls.append(intent_id)
        return {
            "status": "FILLED", "receipt_id": "sim-receipt-1", "fidelity": "SIMULATED_QUOTE",
            "simulated_execution_at_utc": (NOW + timedelta(seconds=1)).isoformat(),
            "fills": [{"fill_id": "fill-1", "quantity": 1, "price": "100.00"}],
        }

    kwargs = {
        "decision_at": NOW,
        "decision_fn": lambda visible: {"side": "BUY", "visible_events": len(visible)},
        "risk_fn": lambda _: {"approved": True},
        "execution_fn": execution,
    }
    if boundary in {"SESSION_PLAN", "SESSION_STATE", "PREFLIGHT_RESULT"}:
        monkeypatch.setattr(DurableJournal, "append", crash_after_append)
        with pytest.raises(RuntimeError, match="crash after"):
            runner.record_preflight(
                _config(), {"approved": True, "status": "PASS"},
            )
        monkeypatch.setattr(DurableJournal, "append", original_append)
        runner.record_preflight(_config(), {"approved": True, "status": "PASS"})
    else:
        runner.record_preflight(_config(), {"approved": True, "status": "PASS"})
        monkeypatch.setattr(DurableJournal, "append", crash_after_append)
        monkeypatch.setattr(DurableJournal, "append_many", crash_after_batch)
        with pytest.raises(RuntimeError, match="crash after"):
            runner.run(_config(), [_event()], **kwargs)
        monkeypatch.setattr(DurableJournal, "append", original_append)
        monkeypatch.setattr(DurableJournal, "append_many", original_append_many)

    recovered = runner.run(
        _config(), [_event()], **kwargs,
        reconcile_execution_fn=lambda _: {
            "status": "NOT_APPLIED", "reconciliation_id": "local-sim-absence-1",
        },
    )
    assert recovered["status"] == "PAPER_COMPLETE"
    assert recovered["execution"]["fills"] == [
        {"fill_id": "fill-1", "quantity": 1, "price": "100.00"},
    ]
    records = DurableJournal(path).records()
    by_type = {}
    for row in records:
        by_type[row.get("t")] = by_type.get(row.get("t"), 0) + 1
    assert by_type["SESSION_PLAN"] == 1
    assert by_type["MARKET_EVENT"] == 1
    assert by_type["PAPER_EXECUTION"] == 1
    assert by_type["OUTBOX_ACK"] == 1
    assert by_type["PAPER_INTENT"] == 1
    assert by_type["DECISION"] == 1
    assert by_type["RISK_VERDICT"] == 1
    assert by_type["SESSION_CLOSED"] == 1
    assert len([fill for row in records if row.get("t") == "PAPER_EXECUTION"
                for fill in row["execution"].get("fills", [])]) == 1
    assert len(execution_calls) == 1
    assert crashed


def test_crash_after_not_applied_reconciliation_does_not_repeat_reconciliation_or_fill(tmp_path, monkeypatch):
    path = tmp_path / "reconciliation.jsonl"
    runner = ForwardSessionRunner(path)
    runner.record_preflight(_config(), {"approved": True, "status": "PASS"})
    execution_attempts = []

    def execution(_decision, _intent_id, _events):
        execution_attempts.append("attempt")
        if len(execution_attempts) == 1:
            raise RuntimeError("no simulation effect before receipt")
        return {
            "status": "FILLED", "receipt_id": "sim-receipt-1", "fidelity": "SIMULATED_QUOTE",
            "simulated_execution_at_utc": (NOW + timedelta(seconds=1)).isoformat(),
            "fills": [{"fill_id": "fill-1", "quantity": 1, "price": "100.00"}],
        }

    kwargs = {
        "decision_at": NOW, "decision_fn": lambda _: {"side": "BUY"},
        "risk_fn": lambda _: {"approved": True}, "execution_fn": execution,
    }
    with pytest.raises(RuntimeError, match="no simulation effect"):
        runner.run(_config(), [_event()], **kwargs)

    original_append = DurableJournal.append
    def crash_after_reconciliation(journal, record):
        sequence = original_append(journal, record)
        if record.get("t") == "OUTBOX_RECONCILED":
            raise RuntimeError("crash after OUTBOX_RECONCILED")
        return sequence

    reconciliation_calls = []
    def not_applied(intent_id):
        reconciliation_calls.append(intent_id)
        return {"status": "NOT_APPLIED", "reconciliation_id": "sim-no-effect-1"}

    monkeypatch.setattr(DurableJournal, "append", crash_after_reconciliation)
    with pytest.raises(RuntimeError, match="crash after OUTBOX_RECONCILED"):
        runner.run(_config(), [_event()], **kwargs, reconcile_execution_fn=not_applied)
    monkeypatch.setattr(DurableJournal, "append", original_append)

    recovered = runner.run(
        _config(), [_event()], **kwargs,
        reconcile_execution_fn=lambda _: pytest.fail("durable NOT_APPLIED proof should be reused"),
    )
    assert recovered["status"] == "PAPER_COMPLETE"
    assert len(reconciliation_calls) == 1
    assert len(execution_attempts) == 2
    records = DurableJournal(path).records()
    assert len([row for row in records if row.get("t") == "OUTBOX_RECONCILED"]) == 1
    assert len([row for row in records if row.get("t") == "PAPER_EXECUTION"]) == 1
    assert len([fill for row in records if row.get("t") == "PAPER_EXECUTION"
                for fill in row["execution"].get("fills", [])]) == 1
