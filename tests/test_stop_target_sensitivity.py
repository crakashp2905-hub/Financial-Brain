from __future__ import annotations

import pytest

from financial_brain.evaluation.stop_target_sensitivity import (
    StopTargetPlan,
    StopTargetSensitivityError,
    evaluate_stop_target_sensitivity,
)


def _plan(**changes):
    values = {"plan_id": "st-sensitivity", "strategy_sha256": "a" * 64,
              "development_data_sha256": "b" * 64, "development_start": "2026-01-02",
              "development_end": "2026-01-03", "protected_final_start": "2026-01-04",
              "protected_final_end": "2026-02-01", "frozen_at_utc": "2026-01-01T00:00:00Z",
              "stop_fractions": (0.1,), "target_fractions": (0.1,), "max_combinations": 1}
    values.update(changes)
    return StopTargetPlan(**values)


def _trade(**changes):
    values = {"split": "DEVELOPMENT", "trade_id": "t1", "session_date": "2026-01-02",
              "decision_sha256": "1" * 64, "side": "BUY", "entry_price": 100.0,
              "notional_inr": 10000.0, "round_trip_costs_inr": 10.0,
              "bars": [{"session_date": "2026-01-02", "open": 100.0, "high": 112.0,
                        "low": 88.0, "close": 108.0, "volume": 1000.0}]}
    values.update(changes)
    return values


def test_same_bar_collision_is_stop_first_and_penalizes_the_trade():
    report = evaluate_stop_target_sensitivity(plan=_plan(), trades=[_trade()])
    assert report["evaluations"][0]["same_bar_adverse_stops"] == 1
    assert report["outcomes"][0]["exit_reason"] == "STOP_AMBIGUOUS_ADVERSE"
    assert report["outcomes"][0]["net_return_fraction"] == pytest.approx(-0.101)
    assert "adverse stop" in report["bar_collision_policy"]


def test_complete_grid_reports_negative_trades_and_costed_outcomes():
    plan = _plan(stop_fractions=(0.05, 0.1), target_fractions=(0.1, 0.2), max_combinations=4)
    trades = [_trade(bars=[{"session_date": "2026-01-02", "open": 100.0, "high": 104.0,
                            "low": 96.0, "close": 102.0, "volume": 1000.0}])]
    report = evaluate_stop_target_sensitivity(plan=plan, trades=trades)
    assert report["combination_count"] == 4
    assert len(report["outcomes"]) == 4
    assert all(row["disposition"] == "DEVELOPMENT_DIAGNOSTIC_ONLY" for row in report["evaluations"])


def test_protected_final_window_and_non_development_rows_are_rejected():
    row = _trade(bars=[{"session_date": "2026-01-04", "open": 100, "high": 101,
                        "low": 99, "close": 100, "volume": 100}])
    with pytest.raises(StopTargetSensitivityError, match="outside the development"):
        evaluate_stop_target_sensitivity(plan=_plan(), trades=[row])
    with pytest.raises(StopTargetSensitivityError, match="DEVELOPMENT"):
        evaluate_stop_target_sensitivity(plan=_plan(), trades=[_trade(split="FINAL")])


def test_invalid_bars_budget_or_parameters_are_rejected():
    with pytest.raises(StopTargetSensitivityError, match="inconsistent"):
        evaluate_stop_target_sensitivity(plan=_plan(), trades=[_trade(bars=[{
            "session_date": "2026-01-02", "open": 100, "high": 99, "low": 95,
            "close": 98, "volume": 1}])])
    with pytest.raises(StopTargetSensitivityError, match="budget"):
        _plan(stop_fractions=(0.1, 0.2), max_combinations=1)
    with pytest.raises(StopTargetSensitivityError, match="between 0 and 1"):
        _plan(target_fractions=(1.0,))
