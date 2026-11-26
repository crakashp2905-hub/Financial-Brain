from datetime import date

import pytest

from financial_brain.research.dual_skill_report import (
    DualSkillError, EconomicObservation, ForecastObservation, build_dual_skill_report,
)


def test_forecast_and_net_economic_skill_are_reported_separately() -> None:
    forecasts = (ForecastObservation("f1", 0.8, 1, 0.5, "a" * 64),
                 ForecastObservation("f2", 0.2, 0, 0.5, "a" * 64))
    economics = (EconomicObservation(date(2026, 1, 1), 0.02, 0.01, "b" * 64),
                 EconomicObservation(date(2026, 1, 2), -0.03, 0.0, "b" * 64))
    report = build_dual_skill_report(forecasts, economics)
    assert report.forecast_brier == pytest.approx(0.04)
    assert report.null_brier == pytest.approx(0.25)
    assert report.forecast_brier_skill == pytest.approx(0.84)
    assert report.mean_session_net_excess == pytest.approx(-0.01)
    assert report.strategy_cumulative_net_return < report.benchmark_cumulative_net_return
    assert report.report_sha256


def test_absent_panel_or_duplicate_forecast_is_not_assumed_success() -> None:
    forecast = ForecastObservation("f1", 0.7, 1, 0.5, "a" * 64)
    economic = EconomicObservation(date(2026, 1, 1), 0.01, 0.0, "b" * 64)
    with pytest.raises(DualSkillError, match="both independent"):
        build_dual_skill_report((forecast,), ())
    with pytest.raises(DualSkillError, match="unique"):
        build_dual_skill_report((forecast, forecast), (economic,))


def test_zero_skill_null_and_unordered_economic_sessions_fail_closed() -> None:
    perfect_null = ForecastObservation("f", 0.4, 0, 0.0, "a" * 64)
    economic = EconomicObservation(date(2026, 1, 1), 0.0, 0.0, "b" * 64)
    with pytest.raises(DualSkillError, match="zero Brier"):
        build_dual_skill_report((perfect_null,), (economic,))
    fs = (ForecastObservation("f", 0.5, 1, 0.5, "a" * 64),)
    reversed_days = (economic, EconomicObservation(date(2026, 1, 1), 0.0, 0.0, "b" * 64))
    with pytest.raises(DualSkillError, match="chronological"):
        build_dual_skill_report(fs, reversed_days)
