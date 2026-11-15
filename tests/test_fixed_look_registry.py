from dataclasses import replace

import pytest

from financial_brain.forward_edge.charter import (
    CharterRegistry,
    EvaluationIdentity,
    ExperimentCharter,
)
from financial_brain.forward_edge.decision_custody import Cohort, CohortSchedule
from financial_brain.forward_edge.fixed_look import FixedLookPolicy
from financial_brain.forward_edge.fixed_look_registry import (
    FixedLookRegistry,
    FixedLookRegistryError,
)
from financial_brain.forward_edge.sample_size import SampleSizePlan, design_sample_size


def _policy() -> FixedLookPolicy:
    return FixedLookPolicy(
        policy_id="risk-look-v1", checkpoints=(20, 40, 60), familywise_alpha=0.05,
        max_drawdown_floor=-0.15, frozen_at_utc="2026-01-01T00:00:00Z",
        first_observation_at_utc="2026-01-02T00:00:00Z",
    )


def _sample_plan(policy) -> SampleSizePlan:
    return design_sample_size(
        design_id="risk-look-sample-v1", detectable_daily_benefit=0.04,
        daily_volatility=0.10, target_power=0.8, familywise_alpha=policy.familywise_alpha,
        planned_looks=len(policy.checkpoints), dependence_design_effect=1.0,
        expected_observation_rate=1.0, session_floor=10,
        frozen_at_utc=policy.frozen_at_utc,
        first_observation_at_utc=policy.first_observation_at_utc,
    )


def _charter_registry(path, policy, sample_plan):
    identity = EvaluationIdentity(
        strategy_code_sha256="a" * 64, model_code_sha256="b" * 64,
        data_snapshot_sha256="c" * 64, data_rights_id="test-rights",
        data_rights_sha256="d" * 64, market_calendar_id="test-calendar",
        market_calendar_sha256="e" * 64, universe_sha256="f" * 64,
        execution_cost_id="test-costs", execution_cost_sha256="1" * 64,
        risk_policy_sha256=FixedLookRegistry.bundle_sha256(policy, sample_plan),
        benchmarks=(("NIFTY50", "2" * 64),),
    )
    charter = ExperimentCharter(
        charter_id="risk-look-cohort-v1", version=1, strategy_id="test-strategy",
        code_sha256="a" * 64, capital_inr="10000", start_on="2026-01-01",
        end_on="2026-12-31", universe=("NSE_EQ",), benchmarks=("NIFTY50",),
        horizon_sessions=5, evaluation_identity=identity,
    )
    registry = CharterRegistry(path)
    schedule = CohortSchedule(charter.sha256(), (Cohort("forward", "2026-01-02", "2026-12-31"),))
    registry.register(charter, schedule)
    return registry


def test_registered_policy_is_recovered_and_evaluated_from_frozen_charter(tmp_path):
    policy = _policy()
    sample_plan = _sample_plan(policy)
    charters = _charter_registry(tmp_path / "charters.jsonl", policy, sample_plan)
    path = tmp_path / "fixed-look.jsonl"
    registry = FixedLookRegistry(path, charters)
    assert registry.register(policy, sample_size_plan=sample_plan,
                             charter_id="risk-look-cohort-v1") == FixedLookRegistry.bundle_sha256(
                                 policy, sample_plan)

    recovered = FixedLookRegistry(path, _charter_registry(
        tmp_path / "charters.jsonl", policy, sample_plan))
    assert recovered.resolve(policy.policy_id) == policy
    result = recovered.evaluate(policy.policy_id, {
        "completed_sessions": 23,
        "lower_confidence_bound": 0.4,
        "observed_max_drawdown": -0.15,
    })
    assert result["status"] == "STOP_FOR_RISK_HARM"
    assert result["policy_sha256"] == policy.sha256()
    assert result["sample_size_plan_sha256"] == sample_plan.sha256()


def test_registered_early_look_withholds_efficacy_until_required_sample_size(tmp_path):
    policy = _policy()
    sample_plan = _sample_plan(policy)
    charters = _charter_registry(tmp_path / "charters.jsonl", policy, sample_plan)
    registry = FixedLookRegistry(tmp_path / "fixed-look.jsonl", charters)
    registry.register(policy, sample_size_plan=sample_plan, charter_id="risk-look-cohort-v1")
    result = registry.evaluate(policy.policy_id, {
        "completed_sessions": 40,
        "lower_confidence_bound": 0.5,
        "observed_max_drawdown": -0.01,
    })
    assert sample_plan.required_sessions > 40
    assert result["status"] == "SAMPLE_SIZE_NOT_MET"
    assert result["efficacy_decision"] == "WITHHELD"


def test_registry_rejects_policy_that_does_not_match_charter_risk_hash(tmp_path):
    policy = _policy()
    sample_plan = _sample_plan(policy)
    charters = _charter_registry(tmp_path / "charters.jsonl", policy, sample_plan)
    altered = FixedLookPolicy(
        policy_id=policy.policy_id, checkpoints=policy.checkpoints, familywise_alpha=0.05,
        max_drawdown_floor=-0.10, frozen_at_utc=policy.frozen_at_utc,
        first_observation_at_utc=policy.first_observation_at_utc,
    )
    registry = FixedLookRegistry(tmp_path / "fixed-look.jsonl", charters)
    with pytest.raises(FixedLookRegistryError, match="hashes differ from the charter risk identity"):
        registry.register(altered, sample_size_plan=sample_plan,
                          charter_id="risk-look-cohort-v1")


def test_registry_rederives_sample_size_instead_of_trusting_dataclass_fields(tmp_path):
    policy = _policy()
    sample_plan = _sample_plan(policy)
    charters = _charter_registry(tmp_path / "charters.jsonl", policy, sample_plan)
    registry = FixedLookRegistry(tmp_path / "fixed-look.jsonl", charters)
    forged = replace(sample_plan, required_sessions=1)
    with pytest.raises(FixedLookRegistryError, match="differs from its derived requirements"):
        registry.register(policy, sample_size_plan=forged,
                          charter_id="risk-look-cohort-v1")


def test_registry_rejects_policy_after_charter_revision(tmp_path):
    policy = _policy()
    sample_plan = _sample_plan(policy)
    charter_path = tmp_path / "charters.jsonl"
    charters = _charter_registry(charter_path, policy, sample_plan)
    registry = FixedLookRegistry(tmp_path / "fixed-look.jsonl", charters)
    registry.register(policy, sample_size_plan=sample_plan,
                      charter_id="risk-look-cohort-v1")
    charter, _schedule, _digest = charters.latest("risk-look-cohort-v1")
    revised = charter.revise(new_start_on="2027-01-01", end_on="2027-12-31", capital_inr="12000")
    revised.require_evaluation_identity()
    revised_schedule = CohortSchedule(revised.sha256(), (Cohort("forward-v2", "2027-01-02", "2027-12-31"),))
    charters.register(revised, revised_schedule)
    recovered = FixedLookRegistry(tmp_path / "fixed-look.jsonl", charters)
    with pytest.raises(FixedLookRegistryError, match="current charter revision"):
        recovered.resolve(policy.policy_id)

