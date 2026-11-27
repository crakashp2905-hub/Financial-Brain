from __future__ import annotations

from datetime import date
import hashlib
import json

import pytest

from financial_brain.forward_edge.prospective_pnl import (
    ProspectivePnlError, ProspectivePnlLedger, build_session_pnl,
)


def _accounting(*, cash: str, positions: dict[str, int], costs: str, realized: str,
                average_cost: dict[str, str], eligible: bool, status: str) -> dict:
    return {"account_id": "paper-1", "cash_inr": cash, "positions": positions,
            "charges_inr": costs, "realized_gross_pnl_inr": realized,
            "average_cost_inr": average_cost, "ledger_balanced": True,
            "cash_reconciled": True, "net_pnl_eligible": eligible, "status": status}


def _fill(day: date = date(2026, 11, 27)) -> dict:
    return {"receipt_id": "fill-1", "instrument_id": "ABC", "side": "BUY",
            "filled_quantity": 10, "session_date": day, "cost_inr": 5.0,
            "evidence_class": "LOCAL_PROSPECTIVE", "pre_entry_sealed": True,
            "receipt_sha256": "a" * 64, "pre_entry_manifest_sha256": "b" * 64}


def _report() -> dict:
    previous = _accounting(cash="10000", positions={}, costs="0", realized="0",
                           average_cost={}, eligible=False, status="RECONCILED_GROSS")
    current = _accounting(cash="8995", positions={"ABC": 10}, costs="5", realized="0",
                          average_cost={"ABC": "100"}, eligible=True, status="RECONCILED_NET")
    return build_session_pnl(
        strategy_id="mom-v1", account_id="paper-1", session=date(2026, 11, 27),
        previous_accounting=previous, current_accounting=current,
        previous_marks={"ABC": 100.0}, current_marks={"ABC": 110.0},
        prospective_fill_receipts=[_fill()], shadow_weights={"ABC": 0.1},
        shadow_starting_equity_inr=10_000.0, shadow_costs_inr=2.0,
        shadow_cost_evidence_sha256="c" * 64, marks_source_sha256="d" * 64)


def test_session_report_reconciles_actual_filled_paper_and_separate_shadow():
    report = _report()
    assert report["paper"]["gross_pnl_inr"] == pytest.approx(100.0)
    assert report["paper"]["transaction_costs_inr"] == pytest.approx(5.0)
    assert report["paper"]["net_pnl_inr"] == pytest.approx(95.0)
    assert report["paper"]["equity_inr"] == pytest.approx(10_095.0)
    assert report["paper"]["session_filled_receipt_count"] == 1
    assert report["shadow"]["gross_pnl_inr"] == pytest.approx(100.0)
    assert report["shadow"]["net_pnl_inr"] == pytest.approx(98.0)
    assert report["shadow_minus_paper_net_pnl_inr"] == pytest.approx(3.0)


def test_pnl_ledger_is_idempotent_immutable_and_monthly():
    ledger = ProspectivePnlLedger(":memory:")
    report = _report()
    assert ledger.append(report) is True
    assert ledger.append(report) is False
    monthly = ledger.monthly_report(strategy_id="mom-v1", year=2026, month=11)
    assert monthly["sessions"] == 1
    assert monthly["paper_filled_sessions"] == 1
    assert monthly["paper_net_pnl_inr"] == pytest.approx(95.0)
    assert monthly["shadow_net_pnl_inr"] == pytest.approx(98.0)
    conflict = {**report, "paper": {**report["paper"], "net_pnl_inr": 96.0}}
    with pytest.raises(ProspectivePnlError, match="digest"):
        ledger.append(conflict)
    unsigned = {key: value for key, value in conflict.items() if key != "sha256"}
    conflict["sha256"] = hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with pytest.raises(ProspectivePnlError, match="immutable"):
        ledger.append(conflict)
    ledger.close()


def test_prospective_pnl_refuses_unsealed_or_non_filled_inventory():
    report = _report()
    kwargs = {"strategy_id": "mom-v1", "account_id": "paper-1",
              "session": date(2026, 11, 27),
              "previous_accounting": _accounting(cash="10000", positions={}, costs="0", realized="0",
                  average_cost={}, eligible=False, status="RECONCILED_GROSS"),
              "current_accounting": _accounting(cash="8995", positions={"ABC": 10}, costs="5", realized="0",
                  average_cost={"ABC": "100"}, eligible=True, status="RECONCILED_NET"),
              "previous_marks": {"ABC": 100.0}, "current_marks": {"ABC": 110.0},
              "prospective_fill_receipts": [{**_fill(), "pre_entry_sealed": False}],
              "shadow_weights": {"ABC": 0.1}, "shadow_starting_equity_inr": 10_000.0,
              "shadow_costs_inr": 2.0, "shadow_cost_evidence_sha256": "c" * 64,
              "marks_source_sha256": "d" * 64}
    with pytest.raises(ProspectivePnlError, match="sealed prospective"):
        build_session_pnl(**kwargs)
    kwargs["prospective_fill_receipts"] = []
    with pytest.raises(ProspectivePnlError, match="do not reconcile"):
        build_session_pnl(**kwargs)
    assert report["paper"]["evidence_class"] == "LOCAL_PROSPECTIVE"


def test_consecutive_session_reports_use_held_fill_inventory_for_actual_pnl():
    previous = _accounting(cash="8995", positions={"ABC": 10}, costs="5", realized="0",
                           average_cost={"ABC": "100"}, eligible=True, status="RECONCILED_NET")
    current = _accounting(cash="8995", positions={"ABC": 10}, costs="5", realized="0",
                          average_cost={"ABC": "100"}, eligible=True, status="RECONCILED_NET")
    report = build_session_pnl(
        strategy_id="mom-v1", account_id="paper-1", session=date(2026, 11, 30),
        previous_accounting=previous, current_accounting=current,
        previous_marks={"ABC": 110.0}, current_marks={"ABC": 105.0},
        prospective_fill_receipts=[_fill()], shadow_weights={"ABC": 0.1},
        shadow_starting_equity_inr=10_100.0, shadow_costs_inr=0.0,
        shadow_cost_evidence_sha256="c" * 64, marks_source_sha256="e" * 64)
    assert report["paper"]["net_pnl_inr"] == pytest.approx(-50.0)
    assert report["paper"]["session_filled_receipt_count"] == 0


def test_unfilled_cash_session_is_recorded_as_cash_not_paper_position_return():
    cash = _accounting(cash="10000", positions={}, costs="0", realized="0",
                       average_cost={}, eligible=False, status="RECONCILED_GROSS")
    report = build_session_pnl(
        strategy_id="mom-v1", account_id="paper-1", session=date(2026, 11, 27),
        previous_accounting=cash, current_accounting=cash,
        previous_marks={}, current_marks={}, prospective_fill_receipts=[],
        shadow_weights={}, shadow_starting_equity_inr=10_000.0,
        shadow_costs_inr=0.0, shadow_cost_evidence_sha256="c" * 64,
        marks_source_sha256="d" * 64)
    assert report["paper"]["evidence_class"] == "CASH_NO_FILLED_POSITIONS"
    assert report["paper"]["position_pnl_eligible"] is False
    assert report["paper"]["net_pnl_inr"] == 0.0
