from __future__ import annotations

import pytest

from financial_brain.forward_edge.matched_benchmark import (
    MatchedBenchmarkError,
    compare_matched_benchmark,
)


def _compare(**overrides):
    args = dict(
        benchmark_id="matched-nifty-sector-control-v1",
        strategy_net_returns=[0.01, -0.005, 0.004],
        benchmark_net_returns=[0.005, -0.004, 0.002],
        strategy_beta=0.95, benchmark_beta=1.0,
        strategy_gross_leverage=1.0, benchmark_gross_leverage=1.05,
        strategy_turnover=0.20, benchmark_turnover=0.21,
        strategy_sector_weights={"IT": 0.4, "BANK": 0.6},
        benchmark_sector_weights={"IT": 0.42, "BANK": 0.58},
    )
    args.update(overrides)
    return compare_matched_benchmark(**args)


def test_matched_benchmark_reports_net_active_returns_after_all_exposure_controls():
    report = _compare()
    assert report["exposure_match"]["status"] == "MATCHED"
    assert report["alpha_claim_allowed_by_exposure_check"] is True
    assert report["mean_daily_net_active_return"] == pytest.approx(
        ((0.01 - 0.005) + (-0.005 + 0.004) + (0.004 - 0.002)) / 3)
    assert report["strategy_compounded_net_return"] == pytest.approx((1.01 * 0.995 * 1.004) - 1)
    assert "serial dependence" in report["limitations"][1]


def test_unmatched_sector_leverage_beta_or_turnover_withholds_alpha():
    report = _compare(strategy_beta=1.5, strategy_gross_leverage=1.5,
                      strategy_turnover=0.5,
                      strategy_sector_weights={"IT": 0.8, "BANK": 0.2})
    assert report["exposure_match"]["status"] == "UNMATCHED"
    assert set(report["exposure_match"]["reasons"]) == {
        "MARKET_BETA_MISMATCH", "GROSS_LEVERAGE_MISMATCH",
        "TURNOVER_MISMATCH", "SECTOR_MIX_MISMATCH"}
    assert report["mean_daily_net_active_return"] is None
    assert report["alpha_claim_allowed_by_exposure_check"] is False


def test_bad_or_unaligned_inputs_are_rejected():
    with pytest.raises(MatchedBenchmarkError, match="aligned"):
        _compare(benchmark_net_returns=[0.01])
    with pytest.raises(MatchedBenchmarkError, match="-100%"):
        _compare(strategy_net_returns=[-1.0, 0, 0])
