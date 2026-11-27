import pytest

from financial_brain.forecasting.index_metrics import score_index_forecasts


def rows():
    return [
        {"session": "2026-11-24", "actual": "UP", "p_up": .8, "p_down": .1, "p_flat": .1,
         "expected_move_abs": .01, "realized_return": .012},
        {"session": "2026-11-24", "actual": "DOWN", "p_up": .1, "p_down": .8, "p_flat": .1,
         "expected_move_abs": .02, "realized_return": -.018},
        {"session": "2026-11-25", "actual": "FLAT", "p_up": .1, "p_down": .1, "p_flat": .8,
         "expected_move_abs": .005, "realized_return": .001},
        {"session": "2026-11-26", "actual": "UP", "p_up": .4, "p_down": .3, "p_flat": .3,
         "expected_move_abs": .02, "realized_return": .01},
    ]


def test_scorecard_reports_multiclass_directional_calibration_and_move_error():
    report = score_index_forecasts(rows(), bootstrap_reps=100, seed=19)
    metrics = report["metrics"]
    assert report["schema"] == "fb.index_forecast_scorecard.v1"
    assert metrics["n"] == 4
    assert metrics["accuracy"] == 1.0
    assert metrics["brier"] == pytest.approx(.18)
    assert metrics["directional_accuracy"] == 1.0
    assert metrics["directional_coverage"] == 1.0
    assert metrics["expected_move_n"] == 4
    assert metrics["expected_move_mae"] == pytest.approx(.0045)
    assert metrics["per_class"]["UP"]["support"] == 2
    assert metrics["per_class"]["UP"]["calibration_gap"] == pytest.approx(-.15)
    assert report["confidence_intervals"]["accuracy"]["lower_95"] is not None
    assert report["confidence_intervals"]["brier"]["upper_95"] is not None
    assert report["resolved_rows_only"] is True
    assert report["unresolved_rows_are_not_scored_as_success"] is True


def test_scorecard_refuses_invalid_probabilities_and_labels():
    with pytest.raises(ValueError, match="supported actual"):
        score_index_forecasts([{"session": "d", "actual": "MAYBE", "p_up": .3,
                                "p_down": .3, "p_flat": .4}])
    with pytest.raises(ValueError, match="sum to one"):
        score_index_forecasts([{"session": "d", "actual": "UP", "p_up": .4,
                                "p_down": .4, "p_flat": .4}])


def test_confidence_intervals_require_multiple_sessions():
    row = rows()[0]
    report = score_index_forecasts([row], bootstrap_reps=50)
    assert report["confidence_intervals"]["accuracy"] == {"lower_95": None, "upper_95": None}
