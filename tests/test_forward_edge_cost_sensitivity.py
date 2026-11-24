"""FE-0061 fixed cost stress scenarios, with explicit estimate provenance."""
import pytest

from financial_brain.costs.india import Segment
from financial_brain.forward_edge.cost_sensitivity import SensitivityError, cost_sensitivity


def test_sensitivity_reports_required_multipliers_and_net_profit_survival() -> None:
    report = cost_sensitivity(gross_pnl=1000, order_value=100_000, base_slippage=100,
                              base_impact=100, calibration_source="observed:paper-trade-sample-1",
                              segment=Segment.INTRADAY, bucket="large")
    assert [row["multiplier"] for row in report.scenarios] == [1.0, 1.5, 2.0]
    assert report.calibrated is True
    assert report.scenarios[0]["net_pnl"] > report.scenarios[2]["net_pnl"]
    assert all(row["net_positive"] == (row["net_pnl"] > 0) for row in report.scenarios)


def test_unobserved_assumptions_are_marked_uncalibrated() -> None:
    report = cost_sensitivity(gross_pnl=10, order_value=100_000, base_slippage=100,
                              base_impact=100, calibration_source="provisional:bucket-model",
                              segment=Segment.DELIVERY, bucket="mid")
    assert report.calibrated is False
    assert not any(row["net_positive"] for row in report.scenarios)


@pytest.mark.parametrize("kwargs", [
    {"base_slippage": None, "base_impact": 1},
    {"base_slippage": -1, "base_impact": 1},
    {"base_slippage": 1, "base_impact": 1, "calibration_source": None},
])
def test_missing_or_invalid_estimates_fail_closed(kwargs) -> None:
    params = dict(gross_pnl=100, order_value=10_000, base_slippage=1, base_impact=1,
                  calibration_source="provisional", segment=Segment.DELIVERY, bucket="large")
    params.update(kwargs)
    with pytest.raises(SensitivityError):
        cost_sensitivity(**params)
