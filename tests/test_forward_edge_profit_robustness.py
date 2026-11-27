from __future__ import annotations

import pytest

from financial_brain.forward_edge.profit_robustness import (
    NetReturnScenario,
    ProfitRobustnessError,
    evaluate_profit_robustness,
)


def test_profit_that_survives_only_optimistic_fills_and_high_beta_is_flagged():
    benchmark = (-0.02, -0.01, 0.0, 0.01, 0.02, -0.005, 0.015, 0.005)
    rf = (1.06 ** (20 / 250)) - 1
    high_beta_no_alpha = tuple(rf + 1.5 * (value - rf) - 0.0002 for value in benchmark)
    report = evaluate_profit_robustness(
        scenarios=(
            NetReturnScenario("base", 1.0, 1.0, "OPTIMISTIC_ESTIMATE", high_beta_no_alpha),
            NetReturnScenario("conservative", 1.5, 1.5, "OBSERVED_QUOTE",
                              (-0.01,) * len(benchmark)),
        ),
        benchmark_returns=benchmark,
    )
    base = report["scenarios"][0]
    assert report["optimistic_fill_dependency"] is True
    assert base["market_beta"] == pytest.approx(1.5)
    assert base["market_alpha"] < 0
    assert base["market_exposure_dependent"] is True
    assert report["market_exposure_dependency_in_any_profitable_scenario"] is True
    assert report["promotion_authorized"] is False


def test_profitable_conservative_fill_scenario_defeats_optimism_flag():
    report = evaluate_profit_robustness(
        scenarios=(
            NetReturnScenario("base", 1.0, 1.0, "BASE_ESTIMATE", (0.01,) * 8),
            NetReturnScenario("stress", 1.5, 1.5, "OBSERVED_QUOTE", (0.002,) * 8),
        ),
        benchmark_returns=(0.0,) * 8,
    )
    assert report["optimistic_fill_dependency"] is False


def test_robustness_requires_aligned_returns_and_a_base_scenario():
    with pytest.raises(ProfitRobustnessError, match="align"):
        evaluate_profit_robustness(
            scenarios=(NetReturnScenario("base", 1, 1, "BASE_ESTIMATE", (0.01, 0.02)),
                       NetReturnScenario("stress", 2, 2, "BASE_ESTIMATE", (0.0,))),
            benchmark_returns=(0.01, 0.02))
    with pytest.raises(ProfitRobustnessError, match="base cost"):
        evaluate_profit_robustness(
            scenarios=(NetReturnScenario("a", 1.2, 1, "BASE_ESTIMATE", (0.01,) * 8),
                       NetReturnScenario("b", 2, 2, "BASE_ESTIMATE", (0.0,) * 8)),
            benchmark_returns=(0.01,) * 8)
