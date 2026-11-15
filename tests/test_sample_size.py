import pytest

from financial_brain.forward_edge.sample_size import (
    SampleSizeError,
    design_sample_size,
)


def _design(**changes):
    args = dict(
        design_id="prospective-momentum-v1", detectable_daily_benefit=0.001,
        daily_volatility=0.02, target_power=0.80, familywise_alpha=0.05,
        planned_looks=1, dependence_design_effect=1.0,
        frozen_at_utc="2026-01-01T00:00:00Z",
        first_observation_at_utc="2026-01-02T00:00:00Z",
    )
    args.update(changes)
    return design_sample_size(**args)


def test_required_sessions_are_calculated_and_sixty_is_only_a_floor():
    plan = _design()
    assert plan.iid_required_sessions > plan.session_floor
    assert plan.required_sessions == plan.iid_required_sessions
    assert plan.power_at_floor < plan.target_power


def test_dependence_missingness_and_multiple_looks_increase_required_sample():
    iid = _design()
    dependent = _design(dependence_design_effect=2.0)
    incomplete = _design(expected_observation_rate=0.8)
    repeated = _design(planned_looks=4)
    assert dependent.required_sessions > iid.required_sessions
    assert incomplete.required_sessions > iid.required_sessions
    assert repeated.required_sessions > iid.required_sessions
    assert dependent.dependence_adjusted_sessions == 2 * iid.iid_required_sessions


def test_easier_benefit_and_higher_power_have_expected_sample_tradeoffs():
    difficult = _design(detectable_daily_benefit=0.0005, target_power=0.90)
    easier = _design(detectable_daily_benefit=0.002, target_power=0.80)
    assert difficult.required_sessions > easier.required_sessions


def test_floor_binds_only_when_the_calculated_design_is_smaller():
    plan = _design(detectable_daily_benefit=0.05, daily_volatility=0.01)
    assert plan.iid_required_sessions < plan.session_floor
    assert plan.required_sessions == plan.session_floor
    assert plan.power_at_floor >= plan.target_power


def test_plan_hash_changes_with_assumptions_and_freeze_must_precede_data():
    plan = _design()
    assert plan.sha256() != _design(dependence_design_effect=1.5).sha256()
    with pytest.raises(SampleSizeError, match="before the first observation"):
        _design(frozen_at_utc="2026-01-02T00:00:00Z")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"detectable_daily_benefit": 0.0}, "benefit and volatility must be positive"),
        ({"daily_volatility": -0.1}, "benefit and volatility must be positive"),
        ({"target_power": 0.5}, "target power"),
        ({"familywise_alpha": 0.5}, "familywise alpha"),
        ({"planned_looks": 0}, "planned looks"),
        ({"dependence_design_effect": 0.9}, "design effect"),
        ({"expected_observation_rate": 0.0}, "observation rate"),
        ({"session_floor": 0}, "session floor"),
    ],
)
def test_invalid_sample_design_parameters_are_rejected(changes, message):
    with pytest.raises(SampleSizeError, match=message):
        _design(**changes)
