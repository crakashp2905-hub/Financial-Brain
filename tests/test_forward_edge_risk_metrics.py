from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.risk_metrics import (
    RiskDay,
    RiskMetricsError,
    portfolio_risk_report,
)


def _rows(values):
    start = date(2026, 1, 1)
    return tuple(RiskDay(start + timedelta(days=i), value, 0 if i % 4 == 0 else 50_000,
                         1_000_000 + i * 500) for i, value in enumerate(values))


def test_risk_metrics_include_cash_days_and_dependence_aware_intervals() -> None:
    values = [0.01, -0.025, 0.004, 0.0, 0.0, -0.012, 0.018] * 8
    report = portfolio_risk_report(_rows(values), expected_block_length=4,
                                   resamples=300, seed=11)
    assert report.sessions == len(values)
    assert report.max_daily_loss.estimate == pytest.approx(0.025)
    assert report.max_drawdown.estimate > 0
    assert report.conditional_drawdown_95.estimate > 0
    assert report.profit_factor.estimate > 0
    assert report.profit_factor.finite_replicates == 300
    assert report.cumulative_turnover_x.estimate > 0
    assert report.max_drawdown.lower <= report.max_drawdown.estimate <= report.max_drawdown.upper


def test_profit_factor_is_unavailable_without_losses_and_cash_turnover_is_zero() -> None:
    rows = _rows([0.0, 0.01, 0.02, 0.0])
    report = portfolio_risk_report(rows, expected_block_length=2, resamples=200)
    assert report.profit_factor.estimate is None
    assert report.profit_factor.lower is None
    assert report.profit_factor.finite_replicates < report.resamples
    assert report.cumulative_turnover_x.estimate > 0  # some rows represent trades


def test_risk_report_rejects_unordered_sessions_and_equity_destroying_loss() -> None:
    rows = _rows([0.0, 0.0, 0.0])
    with pytest.raises(RiskMetricsError, match="chronological"):
        portfolio_risk_report((rows[1], rows[0]), expected_block_length=1)
    with pytest.raises(RiskMetricsError, match="exhaust"):
        RiskDay(date(2026, 1, 1), -1.0, 0, 1000)
