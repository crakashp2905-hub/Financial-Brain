import pytest

from financial_brain.forward_edge.confirmation_bounds import (
    ConfirmationBoundsError,
    confirmation_bounds,
)
from financial_brain.forward_edge.dependence import DependencePolicy


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
