from __future__ import annotations

from datetime import date

import pytest

from financial_brain.forward_edge.performance_report import (
    PerformanceReportError,
    PerformanceSession,
    PerformanceTrade,
    build_performance_report,
)


def _sessions():
    return (
        PerformanceSession(date(2026, 10, 29), 0.02, 1000, 10000, 0.8, 0.5, 3000),
        PerformanceSession(date(2026, 10, 30), -0.03, 1500, 10200, 0.9, 0.4, 3500),
        PerformanceSession(date(2026, 11, 2), -0.01, 500, 9900, 0.6, -0.2, 2500),
    )


def test_report_covers_cost_aware_returns_drawdown_periods_trade_bp_and_capacity():
    report = build_performance_report(
        sessions=_sessions(),
        trades=(PerformanceTrade(10, 1000), PerformanceTrade(-20, 2000)),
        risk_free_daily=0.0001)
    assert report["worst_day_net_return"] == -0.03
    assert report["worst_week_net_return"] == pytest.approx((1.02 * 0.97) - 1)
    assert report["worst_month_net_return"] == pytest.approx((1.02 * 0.97) - 1)
    assert report["longest_consecutive_loss_days"] == 2
    assert report["mean_net_basis_points_per_trade"] == pytest.approx(0.0)
    assert report["notional_weighted_net_basis_points_per_trade"] == pytest.approx(-33.3333333333)
    assert report["capacity_utilization"] == pytest.approx(3000 / 9000)
    assert report["annualized_sharpe"] is not None
    assert report["annualized_sortino"] is not None
    assert report["confidence_intervals"] == "NOT_COMPUTED_USE_STATIONARY_BLOCK_RISK_REPORT"
    assert len(report["report_sha256"]) == 64


def test_missing_or_invalid_performance_data_fails_closed():
    with pytest.raises(PerformanceReportError, match="at least two sessions"):
        build_performance_report(sessions=(_sessions()[0],), trades=(PerformanceTrade(1, 10),))
    with pytest.raises(PerformanceReportError, match="chronological"):
        build_performance_report(sessions=tuple(reversed(_sessions())),
                                 trades=(PerformanceTrade(1, 10),))
    with pytest.raises(PerformanceReportError, match="finite"):
        build_performance_report(
            sessions=(_sessions()[0], _sessions()[1],
                      PerformanceSession(date(2026, 11, 2), float("nan"), 0, 1, 0, 0, 0)),
            trades=(PerformanceTrade(1, 10),))
