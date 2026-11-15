import pytest

from financial_brain.evaluation import factor_regularization as FR


POLICY = FR.RegularizationPolicy(
    factor_penalty_bps_per_unit={"size": 4.0, "momentum": 5.0, "beta": 3.0},
    turnover_cost_bps_per_unit=20.0,
    version="regularizer-v1",
)


def test_comparison_displays_factor_and_turnover_drags_separately():
    report = FR.compare_candidates([
        {"candidate_id": "high-turnover", "forecast_net_alpha_bps": 10.0,
         "expected_turnover_per_session": 0.3,
         "factor_exposures": {"size": 0.1, "momentum": 0.1, "beta": 0.0}},
        {"candidate_id": "factor-loaded", "forecast_net_alpha_bps": 8.0,
         "expected_turnover_per_session": 0.05,
         "factor_exposures": {"size": 0.5, "momentum": 1.0, "beta": 0.5}},
        {"candidate_id": "lower-beta", "forecast_net_alpha_bps": 5.0,
         "expected_turnover_per_session": 0.02,
         "factor_exposures": {"size": 0.0, "momentum": 0.0, "beta": 0.0}},
    ], policy=POLICY, data_split="DEVELOPMENT")
    assert report["candidate_count"] == 3 and report["promotion_eligible"] is False
    by_id = {row["candidate_id"]: row for row in report["candidates"]}
    assert by_id["high-turnover"]["turnover_regularization_drag_bps"] == pytest.approx(6.0)
    assert by_id["factor-loaded"]["factor_regularization_drag_bps"] == pytest.approx(8.5)
    assert by_id["factor-loaded"]["factor_or_turnover_explains_forecast"] is True
    assert by_id["lower-beta"]["regularized_forecast_alpha_bps"] == pytest.approx(4.6)


def test_regularizer_rejects_protected_validation_and_duplicate_candidates():
    row = {"candidate_id": "candidate", "forecast_net_alpha_bps": 2.0,
           "expected_turnover_per_session": 0.1,
           "factor_exposures": {"size": 0.0, "momentum": 0.0, "beta": 0.0}}
    with pytest.raises(FR.FactorRegularizationError, match="development-only"):
        FR.compare_candidates([row], policy=POLICY, data_split="VALIDATION")
    with pytest.raises(FR.FactorRegularizationError, match="unique"):
        FR.compare_candidates([row, row], policy=POLICY, data_split="DEVELOPMENT")
