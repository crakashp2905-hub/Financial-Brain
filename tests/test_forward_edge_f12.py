"""F12 - rehearsal and small-capital rollout tooling (FE-0132 .. FE-0142). Mock only; no broker, no network, no orders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.forward_edge import rehearsal as H
from financial_brain.forward_edge.approvals import Approval

NOW = datetime(2027, 3, 1, 5, 0, tzinfo=timezone.utc)
SHA = "9" * 64


def _approval(kind: str, subject: str, actor="owner:akash", typ="human") -> Approval:
    return Approval(kind, subject, SHA, actor, typ, NOW - timedelta(hours=1), NOW + timedelta(hours=1))


# ---- FE-0132 ----

def test_default_adapter_cannot_send_or_cancel_an_order() -> None:
    a = H.ReadOnlyBrokerAdapter()
    assert a.mode == "READ_ONLY"
    with pytest.raises(H.OrderBlocked):
        a.place_order("INFY", 1)
    with pytest.raises(H.OrderBlocked):
        a.cancel_order("x")


def test_mock_adapter_is_labelled_mock_and_still_cannot_place_orders() -> None:
    m = H.MockBrokerAdapter({"INFY": 10})
    assert m.is_mock and m.positions() == {"INFY": 10}
    assert m.dry_run_order("o1", "INFY", 1) == {"order_id": "o1", "status": "DRY_RUN_ACCEPTED", "mock": True, "filled": 0}
    with pytest.raises(H.OrderBlocked):
        m.place_order("INFY", 1)


# ---- FE-0134 ----

def test_failed_compliance_check_blocks_the_stage_transition() -> None:
    ok, missing = H.compliance_gate({k: True for k in H.COMPLIANCE_ITEMS})
    assert ok and missing == []
    ok, missing = H.compliance_gate({k: True for k in H.COMPLIANCE_ITEMS if k != "registered_static_ip"})
    assert not ok and missing == ["registered_static_ip"]
    assert H.compliance_gate({})[0] is False


# ---- FE-0135 ----

def test_only_clean_real_market_hours_runs_count_and_any_dirt_resets_the_gate() -> None:
    log = H.RehearsalLog(required_clean_runs=3)
    log.record(H.Rehearsal("m1", market_hours=True, mock=True))          # mock runs never count
    assert log.clean_streak == 0
    for i in range(3):
        log.record(H.Rehearsal(f"r{i}", market_hours=True, mock=False))
    assert log.gate_open()
    log.record(H.Rehearsal("bad", market_hours=True, mock=False, unknown_orders=1))
    assert not log.gate_open() and log.clean_streak == 0 and log.resets == 1


def test_off_hours_runs_do_not_count_toward_the_gate() -> None:
    log = H.RehearsalLog(required_clean_runs=1)
    log.record(H.Rehearsal("x", market_hours=False, mock=False))
    assert not log.gate_open()


# ---- FE-0136 ----

@pytest.mark.parametrize("local, broker, expected", [
    ("SUBMITTED", None, "UNKNOWN"), ("PLACED", None, "UNKNOWN"), ("CREATED", None, "CREATED"),
    ("SUBMITTED", "COMPLETE", "FILLED"), ("SUBMITTED", "OPEN", "WORKING"), ("SUBMITTED", "REJECTED", "REJECTED"),
    ("SUBMITTED", "WEIRD", "UNKNOWN"),
])
def test_placed_is_not_filled_without_broker_confirmation(local: str, broker: str | None, expected: str) -> None:
    assert H.reconcile_order(local, broker) == expected


# ---- FE-0137 ----

def test_no_real_trade_before_explicit_human_confirmation_of_the_exact_proposal() -> None:
    prop = H.RiskProposal(D("100000"), D("5000"), D("2000"))
    assert H.proposal_confirmed(prop, None, SHA, NOW) is False
    assert H.proposal_confirmed(prop, _approval("MICRO_RISK_BUDGET", "micro_pilot"), SHA, NOW) is True
    assert H.proposal_confirmed(prop, _approval("MICRO_RISK_BUDGET", "micro_pilot", "agent:x", "agent"), SHA, NOW) is False
    assert H.proposal_confirmed(prop, _approval("MICRO_RISK_BUDGET", "micro_pilot"), "8" * 64, NOW) is False
    with pytest.raises(H.RehearsalError):
        H.RiskProposal(D("100"), D("500"), D("10"))


# ---- FE-0138 ----

def test_statement_reconciliation_reports_every_unexplained_difference() -> None:
    ledger = [{"ref": "t1", "qty": 10, "amount": D("1000"), "fees": D("3")}, {"ref": "t2", "qty": 5, "amount": D("500"), "fees": D("2")}]
    stmt = [{"ref": "t1", "qty": 10, "amount": D("1000"), "fees": D("3.5")}, {"ref": "t3", "qty": 1, "amount": D("10"), "fees": D("0")}]
    out = H.reconcile_statement(ledger, stmt)
    assert out == ["t1: fees differs (3 vs 3.5)", "t2: in ledger, not in statement", "t3: in statement, not in ledger"]
    assert H.reconcile_statement(ledger, ledger) == []


# ---- FE-0139 / FE-0142 ----

@pytest.mark.parametrize("trigger", H.DisarmLatch.TRIGGERS)
def test_each_trigger_disarms_and_restore_needs_a_human(trigger: str) -> None:
    latch = H.DisarmLatch()
    latch.observe(trigger)
    assert latch.armed is False and latch.reason == trigger
    with pytest.raises(H.RehearsalError, match="human"):
        latch.rearm(None, scope_sha256=SHA, now=NOW)
    with pytest.raises(H.RehearsalError, match="cannot approve"):
        latch.rearm(_approval("DISARM_RESET", "disarm_latch", "agent:ops", "agent"), scope_sha256=SHA, now=NOW)
    latch.rearm(_approval("DISARM_RESET", "disarm_latch"), scope_sha256=SHA, now=NOW)
    assert latch.armed is True


def test_unrelated_events_do_not_disarm() -> None:
    latch = H.DisarmLatch()
    latch.observe("HEARTBEAT_OK")
    assert latch.armed is True


# ---- FE-0140 ----

def test_slippage_gap_is_reported_and_execution_models_are_versioned_not_edited() -> None:
    r = H.slippage_report([2.0, 3.0], [4.0, 6.0])
    assert r == {"mean_simulated": 2.5, "mean_observed": 5.0, "mean_gap": 2.5}
    reg = H.ExecutionModelRegistry()
    reg.register("exec-v1", {"slippage_bps": 2.5})
    with pytest.raises(H.RehearsalError, match="new version"):
        reg.register("exec-v1", {"slippage_bps": 5.0})
    reg.register("exec-v2", {"slippage_bps": 5.0})
    assert reg.get("exec-v1")["slippage_bps"] == 2.5


# ---- FE-0141 ----

def test_a_profitable_week_never_scales_capital_by_itself() -> None:
    assert H.scaling_allowed(profitable_week=True, separate_review_done=False, approval_valid=False) is False
    assert H.scaling_allowed(profitable_week=True, separate_review_done=True, approval_valid=False) is False
    assert H.scaling_allowed(profitable_week=False, separate_review_done=True, approval_valid=True) is True
