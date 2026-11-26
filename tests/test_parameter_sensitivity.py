from datetime import datetime, timezone

import pytest

from financial_brain.research.parameter_sensitivity import (
    ParameterSensitivityError, ParameterSensitivityPlan, ParameterVariantResult, analyze_parameter_sensitivity,
)


def _plan() -> ParameterSensitivityPlan:
    return ParameterSensitivityPlan("p1", "lookback", (5.0, 10.0, 20.0),
                                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    datetime(2026, 2, 1, tzinfo=timezone.utc),
                                    "a" * 64, "b" * 64, "c" * 64, "v10")


def _variants(test_scores=(1.0, 0.0, -1.0)) -> tuple[ParameterVariantResult, ...]:
    return (ParameterVariantResult("v5", 5.0, 0.5, 0.2, test_scores[0]),
            ParameterVariantResult("v10", 10.0, 0.9, 0.7, test_scores[1]),
            ParameterVariantResult("v20", 20.0, 0.4, 0.1, test_scores[2]))


def test_center_is_training_selected_even_when_test_favors_another_parameter() -> None:
    report = analyze_parameter_sensitivity(_plan(), _variants((10.0, 0.0, -1.0)))
    assert report["center_variant_id"] == "v10"
    assert report["selection_basis"] == "TRAINING_SCORE_ONLY"
    assert report["test_period_used_for_selection"] is False
    assert "best_test_variant" not in report
    assert report["center_test_score_descriptive_only"] == 0.0


def test_center_not_selected_by_training_score_is_rejected() -> None:
    plan = ParameterSensitivityPlan("p1", "lookback", (5.0, 10.0, 20.0),
                                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    datetime(2026, 2, 1, tzinfo=timezone.utc),
                                    "a" * 64, "b" * 64, "c" * 64, "v5")
    with pytest.raises(ParameterSensitivityError, match="training score only"):
        analyze_parameter_sensitivity(plan, _variants())


def test_post_test_registration_and_changed_grid_are_rejected() -> None:
    with pytest.raises(ParameterSensitivityError, match="before test outcomes"):
        ParameterSensitivityPlan("p", "x", (1.0, 2.0, 3.0), datetime(2026, 2, 1, tzinfo=timezone.utc),
                                 datetime(2026, 2, 1, tzinfo=timezone.utc), "a" * 64, "b" * 64,
                                 "c" * 64, "v")
    wrong_grid = (ParameterVariantResult("v5", 5.0, 1, 1, 1),)
    with pytest.raises(ParameterSensitivityError, match="every frozen"):
        analyze_parameter_sensitivity(_plan(), wrong_grid)
