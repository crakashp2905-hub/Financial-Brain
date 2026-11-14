import pytest

from financial_brain.forward_edge.performance_report import PerformanceReportError, summarize


def _inputs():
    dates = [f"2026-11-{day:02d}" for day in range(2, 10)]
    returns = {day: value for day, value in zip(
        dates, [0.01, -0.005, 0.0, 0.012, -0.004, 0.003, 0.0, 0.006])}
    alpha = {day: value for day, value in zip(
        dates, [0.002, -0.001, 0.0, 0.004, -0.002, 0.001, 0.0, 0.003])}
    turnover = {day: value for day, value in zip(
        dates, [0.2, 0.1, 0.0, 0.3, 0.2, 0.1, 0.0, 0.2])}
    return dates, returns, alpha, turnover


def test_report_declares_sample_denominator_and_assumptions():
    dates, returns, alpha, turnover = _inputs()
    report = summarize(scheduled_sessions=dates, net_returns_by_session=returns,
                       net_alpha_by_session=alpha,
                       one_way_turnover_by_session=turnover)

    assert report["status"] == "COMPLETE"
    assert report["scheduled_denominator"] == 8
    assert report["observed_return_sessions"] == 8
    assert report["sample_start"] == dates[0] and report["sample_end"] == dates[-1]
    assert report["periods_per_year"] == 252
    assert report["metrics"]["mean_daily_net_alpha"] == pytest.approx(sum(alpha.values()) / 8)
    assert report["metrics"]["mean_one_way_turnover"] == pytest.approx(sum(turnover.values()) / 8)
    assert report["metrics"]["sharpe"] is not None
    assert report["metrics"]["profit_factor"] is not None


def test_missing_session_stays_in_denominator_and_withholds_metrics():
    dates, returns, alpha, turnover = _inputs()
    returns.pop(dates[2])
    alpha.pop(dates[2])
    turnover.pop(dates[2])
    report = summarize(scheduled_sessions=dates, net_returns_by_session=returns,
                       net_alpha_by_session=alpha,
                       one_way_turnover_by_session=turnover)

    assert report["status"] == "INCOMPLETE_RETURNS"
    assert report["scheduled_denominator"] == 8
    assert report["observed_return_sessions"] == 7
    assert report["missing_return_sessions"] == [dates[2]]
    assert report["metrics"] is None


def test_report_rejects_extra_unscheduled_data_and_duplicate_schedule():
    dates, returns, alpha, turnover = _inputs()
    with pytest.raises(PerformanceReportError, match="unscheduled"):
        summarize(scheduled_sessions=dates, net_returns_by_session=returns | {"2026-11-10": 0.1},
                  net_alpha_by_session=alpha, one_way_turnover_by_session=turnover)
    with pytest.raises(PerformanceReportError, match="unique"):
        summarize(scheduled_sessions=[dates[0], dates[0]],
                  net_returns_by_session={}, net_alpha_by_session={},
                  one_way_turnover_by_session={})
