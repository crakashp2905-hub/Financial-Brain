from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.bootstrap import (
    BootstrapError,
    DatedAlpha,
    stationary_block_bootstrap_mean,
)


def _obs(values):
    start = date(2026, 1, 1)
    return tuple(DatedAlpha(start + timedelta(days=i), value) for i, value in enumerate(values))


def test_stationary_bootstrap_is_reproducible_and_labels_inference_limits() -> None:
    values = [0.001 * ((i % 7) - 3) for i in range(90)]
    rows = _obs(values)
    first = stationary_block_bootstrap_mean(rows, expected_block_length=8, resamples=600, seed=42)
    second = stationary_block_bootstrap_mean(rows, expected_block_length=8, resamples=600, seed=42)
    assert first == second
    assert first.ci_lower <= first.ci_upper
    assert first.method == "STATIONARY_BLOCK_PERCENTILE_CI"
    assert not first.promotion_inference_allowed


def test_longer_stationary_blocks_retain_dependence_in_clustered_returns() -> None:
    rows = _obs([0.01] * 40 + [-0.01] * 40)
    iid_like = stationary_block_bootstrap_mean(rows, expected_block_length=1,
                                               resamples=1000, seed=9)
    dependent = stationary_block_bootstrap_mean(rows, expected_block_length=20,
                                                resamples=1000, seed=9)
    assert dependent.ci_upper - dependent.ci_lower > iid_like.ci_upper - iid_like.ci_lower


def test_invalid_sample_order_block_length_and_replicate_count_rejected() -> None:
    rows = _obs([0.01, -0.01, 0.02])
    with pytest.raises(BootstrapError, match="chronological"):
        stationary_block_bootstrap_mean((rows[1], rows[0]), expected_block_length=1)
    with pytest.raises(BootstrapError, match="block length"):
        stationary_block_bootstrap_mean(rows, expected_block_length=4)
    with pytest.raises(BootstrapError, match="200"):
        stationary_block_bootstrap_mean(rows, expected_block_length=1, resamples=100)
