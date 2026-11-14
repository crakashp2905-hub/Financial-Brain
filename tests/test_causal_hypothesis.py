from __future__ import annotations

import pytest

from financial_brain.research.causal_hypothesis import (
    HypothesisError,
    HypothesisJournal,
    HypothesisPlan,
    propose_candidate_hypothesis,
)


def _plan(**changes):
    values = {"plan_id": "research-only-v1", "strategy_sha256": "a" * 64,
              "development_data_sha256": "b" * 64, "protocol_sha256": "c" * 64,
              "development_start": "2026-01-02", "development_end": "2026-01-04",
              "protected_validation_start": "2026-01-05", "protected_validation_end": "2026-02-01",
              "frozen_at_utc": "2026-01-01T00:00:00Z", "minimum_sessions": 3,
              "expected_direction": "POSITIVE", "minimum_effect_bp": 4.0,
              "mechanism": "Volatility compression may reduce adverse selection before breakout.",
              "falsifier": "Reject if registered validation delta is below 4 bp or nonpositive in stress."}
    values.update(changes)
    return HypothesisPlan(**values)


def _observations():
    return [
        {"split": "DEVELOPMENT", "session_date": f"2026-01-0{i}", "evidence_id": f"dev:{i}",
         "pattern_net_alpha_bp": delta, "matched_control_net_alpha_bp": 0.0}
        for i, delta in ((2, 10.0), (3, 10.0), (4, -5.0))
    ]


def test_candidate_includes_observed_pattern_precommitted_falsifier_and_no_mutation():
    proposal = propose_candidate_hypothesis(
        plan=_plan(), pattern_id="vol-compression-v1",
        pattern_description="low opening-range width with rising volume", observations=_observations(),
    )
    assert proposal["development_mean_paired_delta_bp"] == pytest.approx(5.0)
    assert proposal["prewritten_falsifier"].startswith("Reject if")
    assert proposal["status"] == "PROPOSED_PENDING_INDEPENDENT_REVIEW"
    assert proposal["paper_strategy_mutation"] == "FORBIDDEN"
    assert proposal["trial_budget_effect"] == "UNCHANGED"
    assert proposal["development_falsifier_triggered"] is False
    assert proposal["claim_scope"].startswith("Development association only")


def test_development_sample_can_trigger_the_predeclared_falsifier_without_rewriting_it():
    rows = _observations()
    for row in rows:
        row["pattern_net_alpha_bp"] = -1.0
    proposal = propose_candidate_hypothesis(plan=_plan(), pattern_id="negative-v1",
                                            pattern_description="predeclared regime", observations=rows)
    assert proposal["development_falsifier_triggered"] is True
    assert "below 4 bp" in proposal["prewritten_falsifier"]


def test_final_window_split_missing_samples_and_duplicate_sessions_fail_closed():
    rows = _observations()
    rows[0]["session_date"] = "2026-01-05"
    with pytest.raises(HypothesisError, match="outside the training"):
        propose_candidate_hypothesis(plan=_plan(), pattern_id="p", pattern_description="pattern", observations=rows)
    rows = _observations()
    rows[0]["split"] = "VALIDATION"
    with pytest.raises(HypothesisError, match="DEVELOPMENT only"):
        propose_candidate_hypothesis(plan=_plan(), pattern_id="p", pattern_description="pattern", observations=rows)
    rows = _observations()[:2]
    with pytest.raises(HypothesisError, match="minimum sessions"):
        propose_candidate_hypothesis(plan=_plan(), pattern_id="p", pattern_description="pattern", observations=rows)
    rows = _observations()
    rows[1]["session_date"] = rows[0]["session_date"]
    with pytest.raises(HypothesisError, match="one matched aggregate"):
        propose_candidate_hypothesis(plan=_plan(), pattern_id="p", pattern_description="pattern", observations=rows)


def test_missing_falsifier_and_bad_evidence_are_rejected():
    with pytest.raises(HypothesisError, match="mechanism and falsifier"):
        _plan(falsifier=" ")
    rows = _observations()
    rows[0]["evidence_id"] = ""
    with pytest.raises(HypothesisError, match="evidence IDs"):
        propose_candidate_hypothesis(plan=_plan(), pattern_id="p", pattern_description="pattern", observations=rows)
    rows = _observations()
    rows[0]["pattern_net_alpha_bp"] = float("nan")
    with pytest.raises(HypothesisError, match="finite"):
        propose_candidate_hypothesis(plan=_plan(), pattern_id="p", pattern_description="pattern", observations=rows)


def test_hypothesis_archive_is_immutable_and_cannot_accept_mutable_strategy_status(tmp_path):
    proposal = propose_candidate_hypothesis(plan=_plan(), pattern_id="p",
                                            pattern_description="pattern", observations=_observations())
    journal = HypothesisJournal(tmp_path / "hypotheses.jsonl")
    digest = journal.record(proposal)
    assert journal.record(proposal) == digest
    changed = {**proposal, "candidate_statement": "different"}
    with pytest.raises(HypothesisError, match="digest"):
        journal.record(changed)
    changed_status = {**proposal, "status": "APPROVED"}
    with pytest.raises(HypothesisError, match="pending independent review"):
        journal.record(changed_status)
