from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.cli import cmd_eod_reconcile
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.paper_eod_gate import EodGateError, PaperEodReconciliationGate
from decimal import Decimal


AS_OF = datetime(2026, 10, 6, 11, tzinfo=timezone.utc)


def _snapshot(*, quantity=2):
    journal = {
        "positions": {"NSE:INFY": {"quantity": 2, "cost_basis": "200"}},
        "fill_ids": ["fill-1"],
        "marks": {"NSE:INFY": {"price": "110", "observed_at": "2026-10-06T10:00:00+00:00"}},
    }
    reference = {
        "reference_positions": {"NSE:INFY": quantity},
        "reference_fill_ids": ["fill-1"],
        "reference_marks": {"NSE:INFY": {"price": "110", "observed_at": "2026-10-06T10:05:00+00:00",
                                                   "source_sha256": "a" * 64}},
    }
    return journal, reference


def _reconcile(gate, day, *, quantity=2, repair_signoff=None):
    journal, reference = _snapshot(quantity=quantity)
    return gate.reconcile(session_date=date.fromisoformat(day), journal_state=journal,
                          as_of=AS_OF, repair_signoff=repair_signoff, **reference)


def test_eod_mismatch_halt_survives_restart_until_clean_human_signoff(tmp_path):
    path = tmp_path / "eod-gate.jsonl"
    gate = PaperEodReconciliationGate(path)
    mismatch = _reconcile(gate, "2026-10-06", quantity=1)
    assert mismatch["status"] == "HALT_MISMATCH"
    assert mismatch["entries_allowed"] is False
    incident = mismatch["incident_id"]

    restarted = PaperEodReconciliationGate(path)
    assert restarted.active_incident() == incident
    assert restarted.entries_allowed() is False
    awaiting = _reconcile(restarted, "2026-10-07")
    assert awaiting["status"] == "AWAITING_REPAIR_SIGNOFF"
    assert awaiting["entries_allowed"] is False

    signed = _reconcile(
        restarted, "2026-10-08",
        repair_signoff={"incident_id": incident, "repair_id": "repair-1", "actor": "owner",
                        "signed_at": "2026-10-06T10:59:00+00:00", "source_sha256": "b" * 64},
    )
    assert signed["status"] == "RECONCILED_SIGNED_OFF"
    assert signed["entries_allowed"] is True
    assert restarted.active_incident() is None
    assert restarted.entries_allowed() is True


def test_eod_gate_rejects_changed_replay_and_wrong_incident_signoff(tmp_path):
    path = tmp_path / "immutable-eod.jsonl"
    gate = PaperEodReconciliationGate(path)
    first = _reconcile(gate, "2026-10-06", quantity=1)
    assert _reconcile(gate, "2026-10-06", quantity=1) == first
    with pytest.raises(EodGateError, match="immutable"):
        _reconcile(gate, "2026-10-06", quantity=2)
    with pytest.raises(EodGateError, match="active EOD incident"):
        _reconcile(gate, "2026-10-07", quantity=2,
                   repair_signoff={"incident_id": "forged", "repair_id": "r", "actor": "owner",
                                   "signed_at": "2026-10-06T10:59:00+00:00", "source_sha256": "b" * 64})


def test_empty_eod_gate_fails_closed(tmp_path):
    gate = PaperEodReconciliationGate(tmp_path / "empty.jsonl")
    assert gate.active_incident() is None
    assert gate.entries_allowed() is False


def test_cli_audits_real_local_journal_and_persists_external_mismatch(tmp_path):
    account_path = tmp_path / "account.jsonl"
    schedule = FeeSchedule("fees-v1", date(2026, 10, 1), Decimal("0"), Decimal("0"))
    PaperAccount(account_path, account_id="eod-cli", initial_cash=Decimal("1000"),
                 fee_schedule=schedule)
    snapshot_path = tmp_path / "reference.json"
    snapshot_path.write_text(
        '{"session_date":"2026-10-06","as_of":"2026-10-06T11:00:00+00:00",'
        '"reference_positions":{"NSE:INFY":1},"reference_fill_ids":[],"reference_marks":{}}',
        encoding="utf-8",
    )
    gate_path = tmp_path / "gate.jsonl"

    report, code = cmd_eod_reconcile(str(account_path), str(snapshot_path), str(gate_path))

    assert code != 0
    assert report["status"] == "HALT_MISMATCH"
    assert report["entries_allowed"] is False
    assert PaperEodReconciliationGate(gate_path).active_incident() == report["incident_id"]
    reopened = PaperAccount(account_path, account_id="eod-cli", initial_cash=Decimal("1000"),
                            fee_schedule=schedule)
    assert reopened.risk_latched is True
