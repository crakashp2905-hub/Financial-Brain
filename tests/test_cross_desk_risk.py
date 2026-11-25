import numpy as np
import pandas as pd
import pytest

from financial_brain.portfolio.cross_desk_risk import (
    CrossDeskRiskError,
    analyze_cross_desk_risk,
)


def _inputs():
    names = ["A", "B"]
    current = pd.DataFrame([[0.04, 0.006], [0.006, 0.0225]], index=names, columns=names)
    shocked = pd.DataFrame([[0.04, 0.018], [0.018, 0.0225]], index=names, columns=names)
    x = np.linspace(-0.12, 0.08, 40)
    current_scenarios = pd.DataFrame({"A": x, "B": -0.35 * x + 0.003})
    shocked_scenarios = pd.DataFrame({"A": x, "B": 0.75 * x - 0.002})
    factors = {"A": {"MARKET": 1.0, "IT": 0.7}, "B": {"MARKET": 0.8, "BANKS": 0.9}}
    return current, shocked, current_scenarios, shocked_scenarios, factors


def test_cross_desk_risk_integrates_current_and_shocked_views():
    current, shocked, scenarios, shock_scenarios, factors = _inputs()
    report = analyze_cross_desk_risk(
        {"A": 60_000, "B": -40_000}, 100_000, current, shocked, factors,
        scenarios, shock_scenarios,
    )

    assert report.gross_exposure_inr == 100_000
    assert report.net_exposure_inr == 20_000
    assert report.position_concentration_hhi == pytest.approx(0.52)
    assert report.factor_exposure_inr == {"BANKS": -36_000, "IT": 42_000, "MARKET": 28_000}
    assert report.current.volatility == pytest.approx(np.sqrt(0.6**2 * 0.04 + 2 * 0.6 * -0.4 * 0.006 + 0.4**2 * 0.0225))
    assert report.shocked.volatility != pytest.approx(report.current.volatility)
    assert sum(report.current.marginal_contribution.values()) == pytest.approx(report.current.volatility)
    assert report.current.expected_shortfall_loss > 0
    assert report.shocked.expected_shortfall_loss != report.current.expected_shortfall_loss
    assert 0.5 <= report.current.risk_contribution_concentration_hhi <= 1.0
    assert report.shocked.risk_contribution_concentration_hhi != pytest.approx(
        report.current.risk_contribution_concentration_hhi
    )
    assert report.current.scenario_count == 40


def test_risk_analysis_refuses_unaligned_or_non_psd_covariance():
    current, shocked, scenarios, shock_scenarios, factors = _inputs()
    bad = shocked.copy()
    bad.loc["A", "B"] = 0.09
    with pytest.raises(CrossDeskRiskError, match="symmetric"):
        analyze_cross_desk_risk({"A": 60, "B": -40}, 100, current, bad, factors,
                                scenarios, shock_scenarios)
    bad = shocked.copy()
    bad.loc["A", "B"] = bad.loc["B", "A"] = 0.1
    with pytest.raises(CrossDeskRiskError, match="positive semidefinite"):
        analyze_cross_desk_risk({"A": 60, "B": -40}, 100, current, bad, factors,
                                scenarios, shock_scenarios)
    with pytest.raises(CrossDeskRiskError, match="cover every"):
        analyze_cross_desk_risk({"A": 60, "B": -40}, 100, current, shocked,
                                {"A": factors["A"]}, scenarios, shock_scenarios)


def test_risk_analysis_requires_enough_finite_tail_scenarios():
    current, shocked, scenarios, shock_scenarios, factors = _inputs()
    with pytest.raises(CrossDeskRiskError, match="at least 20"):
        analyze_cross_desk_risk({"A": 60, "B": -40}, 100, current, shocked, factors,
                                scenarios.iloc[:19], shock_scenarios)
    bad = shock_scenarios.copy()
    bad.loc[0, "A"] = np.nan
    with pytest.raises(CrossDeskRiskError, match="non-finite"):
        analyze_cross_desk_risk({"A": 60, "B": -40}, 100, current, shocked, factors,
                                scenarios, bad)
