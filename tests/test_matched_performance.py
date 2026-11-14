from decimal import Decimal

import pytest

from financial_brain.forward_edge.matched_performance import (
    MatchedPerformanceError,
    from_nav,
    matched_excess,
)


def test_matched_net_alpha_uses_same_sessions_budget_and_flow_adjusted_nav():
    dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    budgets = [Decimal("10000"), Decimal("10000"), Decimal("10000")]
    strategy = from_nav(dates, [100000, 100500, 100500, 100900], [0, 0, 0], budgets)
    benchmark = from_nav(dates, [100000, 100200, 100200, 100400], [0, 0, 0], budgets)

    report = matched_excess(strategy, benchmark)

    assert report["n_sessions"] == 3
    assert report["observations"][1]["strategy_net_return"] == "0.0"  # cash day retained
    assert report["observations"][1]["benchmark_net_return"] == "0.0"
    assert Decimal(report["observations"][0]["net_alpha"]) == Decimal("0.003")

    with_flow = from_nav(["2026-11-02", "2026-11-03"], [100000, 150500], [50000], [10000])
    assert with_flow[0].net_return_fraction == Decimal("0.005")


def test_matched_net_alpha_refuses_date_and_risk_budget_mismatches():
    strategy = from_nav(["2026-11-02", "2026-11-03"], [100000, 100100], [0], [10000])
    benchmark_missing_day = from_nav(["2026-11-02", "2026-11-04"], [100000, 100050], [0], [10000])
    with pytest.raises(MatchedPerformanceError, match="date mismatch"):
        matched_excess(strategy, benchmark_missing_day)

    benchmark_other_budget = from_nav(["2026-11-02", "2026-11-03"], [100000, 100050], [0], [9000])
    with pytest.raises(MatchedPerformanceError, match="risk budget mismatch"):
        matched_excess(strategy, benchmark_other_budget)


def test_nav_contract_rejects_missing_or_duplicate_dates_and_nonpositive_budgets():
    with pytest.raises(MatchedPerformanceError, match="unique"):
        from_nav(["2026-11-02", "2026-11-02"], [100000, 100100], [0], [10000])
    with pytest.raises(MatchedPerformanceError, match="risk budgets"):
        from_nav(["2026-11-02", "2026-11-03"], [100000, 100100], [0], [0])
