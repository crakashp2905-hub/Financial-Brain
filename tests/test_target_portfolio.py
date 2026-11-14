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


def test_no_trade_band_ignores_small_fee_drift_but_reports_material_rebalance():
    common = dict(
        as_of=DAY1, scheduled_rebalances=frozenset({DAY1}), expected_net_alpha={"A": 0.002},
        covariance=np.array([[0.04]]), covariance_names=("A",), max_name_weight=0.40,
        cash_floor=0.10, max_one_way_turnover=0.15, turnover_cost_per_unit=0.0,
        risk_aversion=0.5, no_trade_band=0.01,
    )
    drifted = plan_target_portfolio(previous_weights={"A": 0.105, "CASH": 0.895}, **common)
    assert dict(drifted.weights) == {"A": 0.105, "CASH": 0.895}
    assert drifted.one_way_turnover == 0
    assert drifted.rebalance_triggered is False

    material = plan_target_portfolio(previous_weights={"A": 0.05, "CASH": 0.95}, **common)
    assert dict(material.weights)["A"] == pytest.approx(0.10)
    assert material.rebalance_triggered is True


def test_no_trade_band_must_be_a_finite_weight_fraction():
    with pytest.raises(TargetPortfolioError, match="no-trade band"):
        _plan(no_trade_band=-0.01)


def test_position_and_sector_caps_leave_concentrated_exposure_in_cash():
    names = ("A", "B", "C", "D", "E")
    result = plan_target_portfolio(
        as_of=DAY1,
        scheduled_rebalances=frozenset({DAY1}),
        expected_net_alpha={name: 0.10 - index * 0.001 for index, name in enumerate(names)},
        covariance=np.eye(len(names)) * 0.01,
        covariance_names=names,
        previous_weights={"CASH": 1.0},
        max_name_weight=0.40,
        cash_floor=0.10,
        max_one_way_turnover=1.0,
        risk_aversion=0.0,
        no_trade_band=0.0,
        max_positions=3,
        sectors={"A": "technology", "B": "technology", "C": "consumer",
                 "D": "healthcare", "E": "utilities"},
        max_sector_weight=0.30,
    )
    weights = dict(result.weights)
    invested = {name: weight for name, weight in weights.items() if name != "CASH" and weight > 1e-12}
    assert set(invested) <= {"A", "B", "C"}
    assert len(invested) <= 3
    assert weights["A"] + weights["B"] <= 0.30 + 1e-8
    assert weights["CASH"] >= 0.30 - 1e-8


def test_turnover_limit_cannot_reintroduce_positions_outside_position_budget():
    names = ("A", "B", "C", "D")
    result = plan_target_portfolio(
        as_of=DAY1,
        scheduled_rebalances=frozenset({DAY1}),
        expected_net_alpha={"A": 0.001, "B": 0.002, "C": 0.10, "D": 0.09},
        covariance=np.eye(len(names)) * 0.01,
        covariance_names=names,
        previous_weights={"A": 0.05, "B": 0.05, "CASH": 0.90},
        max_name_weight=0.40,
        cash_floor=0.10,
        max_one_way_turnover=0.01,
        risk_aversion=0.0,
        no_trade_band=0.0,
        max_positions=2,
    )
    weights = dict(result.weights)
    assert weights["A"] > 0
    assert weights["B"] > 0
    assert weights["C"] == 0
    assert weights["D"] == 0
    assert sum(weight > 1e-12 for name, weight in weights.items() if name != "CASH") <= 2


def test_sector_map_must_cover_the_frozen_universe():
    with pytest.raises(TargetPortfolioError, match="every target instrument"):
        _plan(sectors={"A": "technology"})


def test_max_positions_must_be_positive_integer():
    for invalid in (0, -1, 1.5, True):
        with pytest.raises(TargetPortfolioError, match="max_positions"):
            _plan(max_positions=invalid)


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
