import pytest

from financial_brain.learning.risk_distribution import (
    DistributionalRiskError,
    episode_cvar_with_uncertainty,
    quantile_bellman_targets,
    quantile_huber_loss,
)


def test_quantile_bellman_targets_preserve_distribution_and_clip_bounds():
    assert quantile_bellman_targets(1.0, (-2.0, 0.0, 3.0), gamma=0.5, bounds=(-1, 2)) == (0.0, 1.0, 2)


def test_terminal_quantile_target_does_not_bootstrap():
    assert quantile_bellman_targets(-2.0, (), gamma=0.9, terminal=True) == (-2.0,)
    with pytest.raises(DistributionalRiskError, match="terminal"):
        quantile_bellman_targets(-2.0, (1.0,), gamma=0.9, terminal=True)


def test_quantile_target_rejects_invalid_numeric_input():
    with pytest.raises(DistributionalRiskError):
        quantile_bellman_targets(0.0, (float("nan"),), gamma=0.9)


def test_quantile_huber_loss_is_finite_and_zero_at_matching_target():
    assert quantile_huber_loss((1.0,), (1.0,), quantile_levels=(0.5,)) == 0.0
    assert quantile_huber_loss((0.0,), (-2.0, 2.0), quantile_levels=(0.5,)) > 0


def test_episode_cvar_reports_deterministic_uncertainty_interval():
    returns = tuple([0.01] * 38 + [-0.10, -0.20])
    first = episode_cvar_with_uncertainty(returns, bootstrap_samples=500, seed=11)
    second = episode_cvar_with_uncertainty(returns, bootstrap_samples=500, seed=11)
    assert first == second
    assert first.episode_count == 40
    assert first.tail_episode_count == 2
    assert first.cvar_loss == pytest.approx(0.15)
    assert first.lower_cvar_loss <= first.cvar_loss <= first.upper_cvar_loss


def test_episode_cvar_refuses_thin_tail_and_nonfinite_returns():
    with pytest.raises(DistributionalRiskError, match="20 episodes"):
        episode_cvar_with_uncertainty((0.0,) * 19)
    with pytest.raises(DistributionalRiskError, match="finite"):
        episode_cvar_with_uncertainty((0.0,) * 19 + (float("inf"),))
