from __future__ import annotations

import pytest

from financial_brain.forward_edge.discretion_evaluation import (
    DecisionPairOutcome,
    DiscretionEvaluationError,
    evaluate_owner_discretion,
)


def _pair(**overrides):
    values = dict(
        candidate_id="candidate-1",
        model_information_sha256="a" * 64, owner_information_sha256="a" * 64,
        model_market_outcome_sha256="b" * 64, owner_market_outcome_sha256="b" * 64,
        model_cost_schedule_sha256="c" * 64, owner_cost_schedule_sha256="c" * 64,
        model_evaluation_horizon="1D", owner_evaluation_horizon="1D",
        model_default_action="APPROVE", owner_action="REJECT",
        model_capital_budget_inr=100000, owner_capital_budget_inr=100000,
        model_net_pnl_inr=-200, owner_net_pnl_inr=0, costs_complete=True)
    values.update(overrides)
    return DecisionPairOutcome(**values)


def test_owner_override_is_scored_against_model_under_identical_inputs_and_budget():
    report = evaluate_owner_discretion(pairs=(_pair(),))
    assert report["owner_override_count"] == 1
    assert report["owner_override_rate"] == 1
    assert report["mean_owner_minus_model_net_return_on_overrides"] == pytest.approx(0.002)
    row = report["paired_results"][0]
    assert row["comparable"] is True
    assert row["owner_minus_model_net_return"] == pytest.approx(0.002)
    assert report["causal_or_independent_claim"] is False
    assert len(report["evidence_sha256"]) == 64


def test_mismatched_inputs_budget_or_cost_schedule_are_not_scored():
    report = evaluate_owner_discretion(pairs=(
        _pair(),
        _pair(candidate_id="candidate-2", owner_information_sha256="d" * 64),
        _pair(candidate_id="candidate-3", owner_capital_budget_inr=50000),
        _pair(candidate_id="candidate-4", owner_cost_schedule_sha256="e" * 64),
    ))
    assert report["comparable_pairs"] == 1
    assert report["unresolved_or_unmatched_pairs"] == 3
    mismatched = report["paired_results"][1]
    assert mismatched["comparable"] is False
    assert mismatched["owner_minus_model_net_return"] is None
    assert "POINT_IN_TIME_INFORMATION_MISMATCH" in mismatched["unmatched_reasons"]


def test_unknown_costs_duplicate_candidates_and_bad_hashes_are_rejected():
    with pytest.raises(DiscretionEvaluationError, match="complete after-cost"):
        evaluate_owner_discretion(pairs=(_pair(costs_complete=False),))
    with pytest.raises(DiscretionEvaluationError, match="unique candidate"):
        evaluate_owner_discretion(pairs=(_pair(), _pair()))
    with pytest.raises(DiscretionEvaluationError, match="SHA-256"):
        _pair(model_information_sha256="invalid").validate()
