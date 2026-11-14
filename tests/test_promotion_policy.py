from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.promotion_policy import (
    PromotionPolicyError,
    PromotionDecisionLedger,
    PromotionThresholds,
    evaluate_promotion,
    freeze_policy,
)
from financial_brain.forward_edge.sample_size import design_sample_size

NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def _thresholds() -> PromotionThresholds:
    plan = design_sample_size(
        design_id="promotion-sample-v1", detectable_daily_benefit=1.0, daily_volatility=3.0,
        target_power=0.8, familywise_alpha=0.05, planned_looks=1, dependence_design_effect=1.0,
        frozen_at_utc=NOW.isoformat(), first_observation_at_utc=(NOW + timedelta(days=1)).isoformat(),
        session_floor=60,
    )
    return PromotionThresholds(
        "policy-v1", plan, -0.2, 0.95, 0.0, 0.05, 0.6,
        "NIFTY50", "b" * 64, "c" * 64,
    )


def _approval(thresholds: PromotionThresholds, *, actor: str = "owner:123", actor_type: str = "human",
              expiry: datetime | None = None) -> Approval:
    return Approval("PROMOTION_POLICY", thresholds.policy_id, thresholds.sha256(), actor, actor_type,
                    NOW - timedelta(minutes=1), expiry or NOW + timedelta(days=30))


def _frozen():
    thresholds = _thresholds()
    return freeze_policy(thresholds, _approval(thresholds), frozen_at_utc=NOW)


def _evidence(policy) -> dict:
    threshold = policy.thresholds
    session_dates = [(threshold.first_observation_at_utc.date() + timedelta(days=offset)).isoformat()
                     for offset in range(80)]
    return {
        "eligible_session_dates": session_dates,
        "verified_session_dates": session_dates.copy(),
        "observed_max_drawdown_fraction": -0.1,
        "quote_coverage": 0.99,
        "net_alpha_lcb_bps": 1.0,
        "risk_matched_benchmark_alpha_lcb_bps": 0.5,
        "cost_fill_net_alpha_bps": {
            f"{cost:.1f}x@{fill:.2f}": 0.2
            for cost in (1.0, 1.5, 2.0) for fill in (1.0, 0.75, 0.5)
        },
        "adverse_selection_net_alpha_bps": 0.1,
        "multiplicity_adjusted": True,
        "incidents_closed": True,
        "ledger_reconciled": True,
        "data_rights_verified": True,
        "custody_verified": True,
        "benchmark_sha256": threshold.benchmark_sha256,
        "execution_cost_sha256": threshold.execution_cost_sha256,
        "sample_design_sha256": threshold.sample_design_sha256,
        "incident_review_sha256": "d" * 64,
        "ledger_reconciliation_sha256": "e" * 64,
        "data_rights_evidence_sha256": "f" * 64,
        "custody_receipt_sha256": "1" * 64,
    }


def test_policy_requires_human_scoped_approval_and_freeze_before_first_observation() -> None:
    thresholds = _thresholds()
    with pytest.raises(PromotionPolicyError, match="scoped human approval"):
        freeze_policy(thresholds, _approval(thresholds, actor="agent:system"), frozen_at_utc=NOW)
    with pytest.raises(PromotionPolicyError, match="before confirmatory observations"):
        freeze_policy(thresholds, _approval(thresholds), frozen_at_utc=thresholds.first_observation_at_utc)
    with pytest.raises(PromotionPolicyError, match="expires before"):
        freeze_policy(thresholds, _approval(thresholds, expiry=NOW + timedelta(hours=1)), frozen_at_utc=NOW)


def test_promotion_alpha_must_match_frozen_sample_design() -> None:
    from dataclasses import replace

    thresholds = _thresholds()
    with pytest.raises(PromotionPolicyError, match="must match"):
        replace(thresholds, familywise_alpha=0.01)


def test_complete_policy_evidence_passes_only_when_all_bound_hashes_match() -> None:
    policy = _frozen()
    evidence = _evidence(policy)
    result = evaluate_promotion(policy, evidence)
    assert result["verdict"] == "PROMOTION_GRADE"
    assert result["promotable"] is True

    evidence["execution_cost_sha256"] = "f" * 64
    mismatch = evaluate_promotion(policy, evidence)
    assert mismatch["promotable"] is False
    assert "EXECUTION_COST_SHA256_MISMATCH" in mismatch["reasons"]


def test_missing_or_failed_gates_never_default_to_promotion() -> None:
    policy = _frozen()
    assert evaluate_promotion(policy, {})["verdict"] == "INSUFFICIENT_EVIDENCE"
    evidence = _evidence(policy)
    grid = dict(evidence["cost_fill_net_alpha_bps"])
    grid["2.0x@0.50"] = -0.1
    few_sessions = evidence["eligible_session_dates"][:10]
    evidence.update({"eligible_session_dates": few_sessions, "verified_session_dates": few_sessions.copy(),
                     "order_count": 1000000, "observed_max_drawdown_fraction": -0.3,
                     "quote_coverage": 0.5, "net_alpha_lcb_bps": -1,
                     "cost_fill_net_alpha_bps": grid, "adverse_selection_net_alpha_bps": -0.1,
                     "multiplicity_adjusted": False,
                     "incidents_closed": False})
    result = evaluate_promotion(policy, evidence)
    assert result["promotable"] is False
    assert {"INSUFFICIENT_INDEPENDENT_SESSIONS", "DRAWDOWN_LIMIT_BREACHED",
            "QUOTE_COVERAGE_BELOW_POLICY", "NET_ALPHA_LOWER_BOUND_BELOW_POLICY",
            "COST_FILL_STRESS_NOT_POSITIVE", "MULTIPLICITY_NOT_ADJUSTED",
            "ADVERSE_SELECTION_STRESS_NOT_POSITIVE", "INCIDENTS_CLOSED_NOT_CONFIRMED"}.issubset(result["reasons"])


def test_missing_stress_cell_or_adverse_selection_blocks_promotion() -> None:
    policy = _frozen()
    evidence = _evidence(policy)
    del evidence["cost_fill_net_alpha_bps"]["2.0x@0.50"]
    assert "COST_FILL_STRESS_GRID_INCOMPLETE" in evaluate_promotion(policy, evidence)["reasons"]
    evidence = _evidence(policy)
    evidence["adverse_selection_net_alpha_bps"] = 0
    assert "ADVERSE_SELECTION_STRESS_NOT_POSITIVE" in evaluate_promotion(policy, evidence)["reasons"]


def test_risk_matched_benchmark_and_artifact_receipts_are_required() -> None:
    policy = _frozen()
    evidence = _evidence(policy)
    evidence["net_alpha_lcb_bps"] = 5.0
    evidence["risk_matched_benchmark_alpha_lcb_bps"] = -0.1
    evidence["custody_receipt_sha256"] = "not-a-digest"
    result = evaluate_promotion(policy, evidence)
    assert "RISK_MATCHED_BENCHMARK_ALPHA_NOT_POSITIVE" in result["reasons"]
    assert "INVALID:custody_receipt_sha256" in result["reasons"]


def test_duplicate_or_unverified_session_dates_do_not_inflate_sample_size() -> None:
    policy = _frozen()
    evidence = _evidence(policy)
    evidence["eligible_session_dates"] = [evidence["eligible_session_dates"][0]] * 80
    result = evaluate_promotion(policy, evidence)
    assert "DUPLICATE:eligible_session_dates" in result["reasons"]
    evidence = _evidence(policy)
    evidence["verified_session_dates"] = evidence["verified_session_dates"][:-1]
    result = evaluate_promotion(policy, evidence)
    assert "INDEPENDENT_SESSION_RECEIPTS_INCOMPLETE" in result["reasons"]


def test_decision_ledger_is_immutable_chained_and_never_grants_cash_permission(tmp_path) -> None:
    policy = _frozen()
    ledger = PromotionDecisionLedger(tmp_path / "promotion.jsonl")
    evidence = {"eligible_independent_sessions": 1}
    first = ledger.record(decision_id="decision-1", cohort_id="cohort-1", policy=policy,
                          evidence=evidence, decided_at_utc=NOW + timedelta(hours=1))
    assert first["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert first["physical_orders"] is False
    assert first["live_cash_permission"] is False
    assert ledger.record(decision_id="decision-1", cohort_id="cohort-1", policy=policy,
                         evidence=evidence, decided_at_utc=NOW + timedelta(hours=1)) == first
    with pytest.raises(PromotionPolicyError, match="immutable"):
        ledger.record(decision_id="decision-1", cohort_id="cohort-1", policy=policy,
                      evidence={"eligible_independent_sessions": 2},
                      decided_at_utc=NOW + timedelta(hours=1))
    assert ledger.records() == [first]


def test_decision_ledger_detects_tampering(tmp_path) -> None:
    policy = _frozen()
    path = tmp_path / "promotion.jsonl"
    ledger = PromotionDecisionLedger(path)
    ledger.record(decision_id="decision-1", cohort_id="cohort-1", policy=policy,
                  evidence={}, decided_at_utc=NOW + timedelta(hours=1))
    path.write_text(path.read_text(encoding="utf-8").replace("INSUFFICIENT_EVIDENCE", "PROMOTION_GRADE"),
                    encoding="utf-8")
    with pytest.raises(PromotionPolicyError, match="corrupt"):
        ledger.records()
