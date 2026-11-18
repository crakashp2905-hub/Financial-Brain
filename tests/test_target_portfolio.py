from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from financial_brain.forward_edge.target_portfolio import (
    TargetPortfolioError,
    cash_days,
    plan_target_portfolio,
)


DAY1 = date(2027, 1, 4)
DAY2 = date(2027, 1, 5)


def _plan(**over):
    args = dict(
        as_of=DAY1, scheduled_rebalances=frozenset({DAY1}),
        expected_net_alpha={"A": 0.03, "B": 0.025},
        covariance=np.array([[0.04, 0.01], [0.01, 0.03]]), covariance_names=("A", "B"),
        previous_weights={"A": 0.05, "B": 0.05, "CASH": 0.90}, max_name_weight=0.40,
        cash_floor=0.10, max_one_way_turnover=0.15, turnover_cost_per_unit=0.001,
        risk_aversion=0.5,
    )
    args.update(over)
    return plan_target_portfolio(**args)


def test_scheduled_rebalance_returns_bounded_target_weights_and_cash() -> None:
    result = _plan()
    weights = dict(result.weights)
    assert result.scheduled_rebalance
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["CASH"] >= 0.10
    assert max(weights["A"], weights["B"]) <= 0.40
    assert result.one_way_turnover <= 0.15 + 1e-8
    assert result.turnover_cost == pytest.approx(0.001 * result.one_way_turnover)
    assert result.utility == pytest.approx(result.expected_net_alpha - result.risk_penalty - result.turnover_cost)


def test_unscheduled_observation_preserves_previous_book() -> None:
    result = _plan(as_of=DAY2)
    assert not result.scheduled_rebalance
    assert dict(result.weights) == {"A": 0.05, "B": 0.05, "CASH": 0.90}
    assert result.one_way_turnover == 0
    assert result.expected_net_alpha == 0


def test_turnover_cost_can_keep_a_paper_book_in_cash() -> None:
    result = _plan(expected_net_alpha={"A": 0.002, "B": 0.001},
                   previous_weights={"A": 0.0, "B": 0.0, "CASH": 1.0},
                   max_one_way_turnover=0.5, turnover_cost_per_unit=0.10)
    assert dict(result.weights) == {"A": 0.0, "B": 0.0, "CASH": 1.0}
    assert result.utility == 0


def test_cash_days_count_explicit_cash_and_rejects_bad_ledgers() -> None:
    assert cash_days([{"A": 0.8, "CASH": 0.2}, {"A": 1.0, "CASH": 0.0}]) == 1
    with pytest.raises(TargetPortfolioError, match="sum to one"):
        cash_days([{"A": 0.9, "CASH": 0.2}])


def test_invalid_covariance_and_previous_cash_floor_fail_closed() -> None:
    with pytest.raises(TargetPortfolioError, match="positive semidefinite"):
        _plan(covariance=np.array([[1.0, 2.0], [2.0, 1.0]]))
    with pytest.raises(TargetPortfolioError, match="cash floor"):
        _plan(previous_weights={"A": 0.5, "B": 0.4, "CASH": 0.1})
    with pytest.raises(TargetPortfolioError, match="outside the frozen target universe"):
        _plan(previous_weights={"A": 0.05, "B": 0.05, "OLD": 0.10, "CASH": 0.80})
