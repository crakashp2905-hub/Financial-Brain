import pytest

from financial_brain.forward_edge.execution_stress import ExecutionStressError, build_execution_stress_report


def test_full_intention_stress_grid_includes_missed_and_partial_intentions_deterministically():
    rows = [
        {"intention_id": "filled", "gross_pnl_full_fill_inr": 500, "cost_full_fill_inr": 100},
        {"intention_id": "partial", "gross_pnl_full_fill_inr": 100, "cost_full_fill_inr": 50},
        {"intention_id": "missed", "gross_pnl_full_fill_inr": -100, "cost_full_fill_inr": 0},
    ]
    report = build_execution_stress_report(rows, nav_inr=10_000)
    assert report["evidence_class"] == "SCENARIO_STRESS"
    assert report["intention_count"] == 3
    assert report["scenarios"]["1x@100%"]["net_pnl_inr"] == pytest.approx(350)
    assert report["scenarios"]["2x@50%"]["net_pnl_inr"] == pytest.approx(100)
    assert report["robust_economics"] is True
    assert report["intention_population_sha256"] == build_execution_stress_report(rows[::-1], nav_inr=10_000)[
        "intention_population_sha256"]


def test_fragile_candidate_fails_grid_and_missing_intentions_are_not_silently_dropped():
    rows = [{"intention_id": "fragile", "gross_pnl_full_fill_inr": 120, "cost_full_fill_inr": 80}]
    report = build_execution_stress_report(rows, nav_inr=10_000)
    assert report["promotion_gate"]["status"] == "FAIL"
    assert "2x@100%" in report["promotion_gate"]["failed"]
    with pytest.raises(ExecutionStressError, match="missing numeric"):
        build_execution_stress_report(rows + [{"intention_id": "unpriced"}], nav_inr=10_000)
    with pytest.raises(ExecutionStressError, match="unique"):
        build_execution_stress_report(rows + rows, nav_inr=10_000)
