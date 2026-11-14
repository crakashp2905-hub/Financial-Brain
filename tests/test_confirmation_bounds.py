from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.confirmation_bounds import (
    ConfirmationBoundsError,
    confirmation_bounds,
)
from financial_brain.forward_edge.dependence import DependencePolicy
from financial_brain.forward_edge.prospective_cohort import freeze_champion
from financial_brain.forward_edge.prospective_registry import ProspectiveCohortRegistry
from financial_brain.forward_edge.sample_size import design_sample_size
from financial_brain.forward_edge.confirmation_bounds import (
    confirmation_session_outcome_sha256,
    registered_confirmation_bounds,
)


def _policy(alpha=0.05, lags=1):
    return DependencePolicy(
        policy_id="frozen-confirmation-v1", hac_lags=lags, bootstrap_mean_block=2.0,
        bootstrap_resamples=200, bootstrap_seed=73, alpha=alpha,
        frozen_at_utc="2026-01-01T00:00:00+00:00",
        first_observation_at_utc="2026-01-02T00:00:00+00:00",
    )


def _call(alpha, utility, **kwargs):
    sessions = [f"2026-01-{day:02d}" for day in range(2, 2 + len(alpha))]
    return confirmation_bounds(
        scheduled_sessions=sessions,
        daily_net_alpha=dict(zip(sessions, alpha, strict=True)),
        after_cost_portfolio_utility=dict(zip(sessions, utility, strict=True)),
        policy=_policy(**kwargs),
    )


def test_confirmation_requires_positive_bounds_for_both_net_alpha_and_utility():
    result = _call([0.01, 0.012, 0.009, 0.011, 0.013],
                   [0.004, 0.005, 0.0045, 0.006, 0.0055])
    assert result["status"] == "CONFIRMED"
    assert result["daily_net_alpha"]["lower_confidence_bound"] > 0
    assert result["after_cost_portfolio_utility"]["lower_confidence_bound"] > 0
    assert result["per_metric_alpha"] == pytest.approx(0.025)
    assert result["familywise_alpha"] == pytest.approx(0.05)
    assert result["daily_net_alpha"]["confidence_level"] == pytest.approx(0.975)


def test_positive_point_estimate_with_nonpositive_lower_bound_is_not_confirmed():
    result = _call([0.06, -0.04, 0.06, -0.04, 0.01],
                   [0.02, 0.01, 0.03, 0.02, 0.025])
    alpha = result["daily_net_alpha"]
    assert alpha["point_estimate"] > 0
    assert alpha["lower_confidence_bound"] <= 0
    assert alpha["status"] == "NOT_CONFIRMED"
    assert result["status"] == "NOT_CONFIRMED"


def test_missing_scheduled_values_are_not_dropped_or_confirmed():
    sessions = ["2026-01-02", "2026-01-03", "2026-01-04"]
    result = confirmation_bounds(
        scheduled_sessions=sessions,
        daily_net_alpha={sessions[0]: 0.02, sessions[1]: 0.02},
        after_cost_portfolio_utility={day: 0.01 for day in sessions},
        policy=_policy(),
    )
    assert result["scheduled_denominator"] == 3
    assert result["daily_net_alpha"]["observed_sessions"] == 2
    assert result["daily_net_alpha"]["missing_sessions"] == [sessions[2]]
    assert result["daily_net_alpha"]["lower_confidence_bound"] is None
    assert result["status"] == "NOT_CONFIRMED"


def test_degenerate_variance_does_not_create_a_zero_width_confirmation():
    result = _call([0.01] * 4, [0.02] * 4)
    assert result["daily_net_alpha"]["status"] == "UNDEFINED_VARIANCE"
    assert result["daily_net_alpha"]["lower_confidence_bound"] is None
    assert result["status"] == "NOT_CONFIRMED"


def test_extra_dates_bad_schedule_and_nonfinite_values_are_rejected():
    with pytest.raises(ConfirmationBoundsError, match="unscheduled"):
        confirmation_bounds(
            scheduled_sessions=["2026-01-02", "2026-01-03", "2026-01-04"],
            daily_net_alpha={"2026-01-02": 0.1, "2026-01-03": 0.1, "2026-01-04": 0.1,
                             "2026-01-05": 0.1},
            after_cost_portfolio_utility={"2026-01-02": 0.1, "2026-01-03": 0.1,
                                          "2026-01-04": 0.1},
            policy=_policy(),
        )
    with pytest.raises(ConfirmationBoundsError, match="unique and ordered"):
        confirmation_bounds(
            scheduled_sessions=["2026-01-03", "2026-01-02", "2026-01-04"],
            daily_net_alpha={"2026-01-02": 0.1, "2026-01-03": 0.2, "2026-01-04": 0.3},
            after_cost_portfolio_utility={"2026-01-02": 0.1, "2026-01-03": 0.2,
                                          "2026-01-04": 0.3},
            policy=_policy(),
        )
    with pytest.raises(ConfirmationBoundsError, match="finite"):
        _call([0.1, float("nan"), 0.2], [0.1, 0.2, 0.3])


def _registered_inputs(tmp_path, *, required_floor=5, planned_looks=1):
    sessions = [f"2026-01-{day:02d}" for day in range(6, 11)]
    cohort = freeze_champion(
        development_returns={"candidate": [0.01] * 5},
        development_sessions=[f"2026-01-{day:02d}" for day in range(1, 6)],
        confirmation_sessions=sessions,
        frozen_at=datetime(2026, 1, 5, 15, tzinfo=timezone.utc),
        first_confirmation_observation_at=datetime(2026, 1, 6, 3, tzinfo=timezone.utc),
    )
    policy = DependencePolicy(
        policy_id="registered-bounds-v1", hac_lags=1, bootstrap_mean_block=2.0,
        bootstrap_resamples=200, bootstrap_seed=5, alpha=0.05,
        frozen_at_utc=cohort.frozen_at_utc,
        first_observation_at_utc=cohort.first_confirmation_observation_at_utc,
    )
    sample_plan = design_sample_size(
        design_id="registered-bounds-sample-v1", detectable_daily_benefit=0.5,
        daily_volatility=0.1, target_power=0.8, familywise_alpha=policy.alpha,
        planned_looks=planned_looks, dependence_design_effect=1.0,
        expected_observation_rate=1.0, session_floor=required_floor,
        frozen_at_utc=cohort.frozen_at_utc,
        first_observation_at_utc=cohort.first_confirmation_observation_at_utc,
    )
    alpha = {day: 0.01 + i * 0.0005 for i, day in enumerate(sessions)}
    utility = {day: 0.005 + i * 0.0002 for i, day in enumerate(sessions)}
    registry = ProspectiveCohortRegistry(tmp_path / "cohort.jsonl")
    registry.register("confirmation-v1", cohort)
    for day in sessions:
        registry.record_observation(
            cohort_id="confirmation-v1", session=day,
            observed_at=datetime.fromisoformat(day + "T04:00:00+00:00"),
            outcome_sha256=confirmation_session_outcome_sha256(
                session=day, daily_net_alpha=alpha,
                after_cost_portfolio_utility=utility,
            ),
        )
    return sessions, registry, policy, sample_plan, alpha, utility


def test_registered_confirmation_binds_cohort_receipts_and_sample_plan(tmp_path):
    sessions, registry, policy, sample_plan, alpha, utility = _registered_inputs(tmp_path)
    report = registered_confirmation_bounds(
        cohort_registry=registry, cohort_id="confirmation-v1",
        daily_net_alpha=alpha, after_cost_portfolio_utility=utility,
        policy=policy, sample_size_plan=sample_plan,
    )
    assert report["status"] == "CONFIRMED"
    assert report["cohort_plan_sha256"] == registry.resolve("confirmation-v1").plan_sha256
    assert report["sample_size_plan_sha256"] == sample_plan.sha256()
    assert report["required_sessions"] <= len(sessions)
    assert report["observed_sessions"] == len(sessions)


def test_registered_confirmation_rejects_changed_outcome_values(tmp_path):
    sessions, registry, policy, sample_plan, alpha, utility = _registered_inputs(tmp_path)
    alpha[sessions[0]] += 0.2
    with pytest.raises(ConfirmationBoundsError, match="do not match"):
        registered_confirmation_bounds(
            cohort_registry=registry, cohort_id="confirmation-v1",
            daily_net_alpha=alpha, after_cost_portfolio_utility=utility,
            policy=policy, sample_size_plan=sample_plan,
        )


def test_registered_confirmation_withholds_when_design_requires_more_sessions(tmp_path):
    _sessions, registry, policy, sample_plan, alpha, utility = _registered_inputs(
        tmp_path, required_floor=10,
    )
    report = registered_confirmation_bounds(
        cohort_registry=registry, cohort_id="confirmation-v1",
        daily_net_alpha=alpha, after_cost_portfolio_utility=utility,
        policy=policy, sample_size_plan=sample_plan,
    )
    assert report["required_sessions"] == 10
    assert report["status"] == "SAMPLE_SIZE_NOT_MET"
    assert report["daily_net_alpha"]["lower_confidence_bound"] is None
    assert report["after_cost_portfolio_utility"]["lower_confidence_bound"] is None


def test_confirmation_alpha_is_allocated_across_metrics_and_registered_looks(tmp_path):
    _sessions, registry, policy, sample_plan, alpha, utility = _registered_inputs(
        tmp_path, planned_looks=3,
    )
    report = registered_confirmation_bounds(
        cohort_registry=registry, cohort_id="confirmation-v1",
        daily_net_alpha=alpha, after_cost_portfolio_utility=utility,
        policy=policy, sample_size_plan=sample_plan,
    )
    assert report["planned_looks"] == 3
    assert report["metric_family_size"] == 6
    assert report["per_metric_alpha"] == pytest.approx(0.05 / 6)
