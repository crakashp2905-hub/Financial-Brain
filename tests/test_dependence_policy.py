import numpy as np
import pytest

from financial_brain.forward_edge.dependence import (
    DependenceError,
    DependencePolicy,
    summarize_dependence,
)


def _policy(**changes):
    base = dict(policy_id="cohort-a-v1", hac_lags=5, bootstrap_mean_block=8.0,
                bootstrap_resamples=200, bootstrap_seed=17, alpha=0.05,
                frozen_at_utc="2026-10-01T12:00:00Z",
                first_observation_at_utc="2026-10-05T03:45:00Z")
    return DependencePolicy(**(base | changes))


def test_policy_is_hashable_and_must_precede_observations():
    policy = _policy()
    assert len(policy.sha256()) == 64
    assert _policy(hac_lags=6).sha256() != policy.sha256()
    with pytest.raises(DependenceError, match="before the first observation"):
        _policy(frozen_at_utc="2026-10-06T12:00:00Z")


def test_dependence_summary_reports_effective_sample_and_fixed_bootstrap():
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.01, 400)
    values = []
    previous = 0.0
    for value in noise:
        previous = 0.7 * previous + float(value)
        values.append(previous + 0.0005)
    policy = _policy()

    result = summarize_dependence(values, policy)
    repeated = summarize_dependence(values, policy)

    assert result == repeated
    assert result["observations"] == 400
    assert result["effective_sample_size"] < 400
    assert result["hac_lags"] == 5 and result["bootstrap_mean_block"] == 8.0
    assert result["bootstrap_seed"] == 17 and result["bootstrap_resamples"] == 200
    assert result["policy_sha256"] == policy.sha256()


def test_policy_and_sample_contracts_fail_closed():
    with pytest.raises(DependenceError, match="timezone-aware"):
        _policy(frozen_at_utc="2026-10-01T12:00:00")
    with pytest.raises(DependenceError, match="at least 100"):
        _policy(bootstrap_resamples=20)
    with pytest.raises(DependenceError, match="smaller than the sample"):
        summarize_dependence([0.01, -0.01, 0.02], _policy(hac_lags=3))
    with pytest.raises(DependenceError, match="finite observations"):
        summarize_dependence([0.01, float("nan"), -0.01], _policy(hac_lags=1))
