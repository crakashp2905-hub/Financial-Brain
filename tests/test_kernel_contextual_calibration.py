from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.contextual_calibration import (
    CalibrationContext,
    evaluate_contextual_calibration,
)
from financial_brain.kernel.probability_calibration import (
    CalibrationError,
    CalibrationPolicy,
    ForecastObservation,
)


UTC = timezone.utc
HASH = "a" * 64
TRAIN_END = datetime(2026, 10, 31, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 11, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 12, 1, tzinfo=UTC)
AS_OF = datetime(2026, 11, 27, tzinfo=UTC)


def _policy():
    return CalibrationPolicy("context-cal", "v1", HASH, TRAIN_END, HOLDOUT_START,
                              HOLDOUT_END, minimum_observations_per_bucket=30,
                              probability_bins=10)


def _observations(count=60):
    rows = []
    contexts = {}
    for index in range(count):
        predicted = HOLDOUT_START + timedelta(hours=index)
        group = "OPEN" if index < count // 2 else "CLOSE"
        forecast_id = f"forecast-{index}"
        rows.append(ForecastObservation(
            forecast_id, 0.70, int(index % 3 != 0), predicted,
            predicted + timedelta(minutes=30), HASH, f"{index + 1:064x}", "placeholder"))
        contexts[forecast_id] = CalibrationContext("TRENDING", group, predicted - timedelta(minutes=1))
    return tuple(rows), contexts


def test_calibration_is_separated_by_point_in_time_regime_and_time_of_day():
    rows, contexts = _observations()
    report = evaluate_contextual_calibration(policy=_policy(), observations=rows,
                                             contexts=contexts, evaluation_as_of=AS_OF)
    assert report["execution_authorization"] is False
    assert report["overall_status"] == "CALIBRATION_EVALUATED_NOT_PROMOTION"
    assert [(row["regime_bucket"], row["time_of_day_bucket"], row["sample_count"])
            for row in report["group_reports"]] == [
                ("TRENDING", "CLOSE", 30), ("TRENDING", "OPEN", 30)]
    for group in report["group_reports"]:
        assert group["70_percent_probability_bins"][0]["probability_bin"] == 7
        assert group["70_percent_probability_bins"][0]["status"] == "OUT_OF_TIME_BUCKET_SAMPLE_SUFFICIENT"
    assert len(report["evidence_sha256"]) == 64


def test_context_known_after_forecast_and_missing_context_are_rejected():
    rows, contexts = _observations(2)
    first = rows[0]
    contexts[first.forecast_id] = CalibrationContext("TRENDING", "OPEN",
                                                      first.predicted_at + timedelta(seconds=1))
    with pytest.raises(CalibrationError, match="available by forecast"):
        evaluate_contextual_calibration(policy=_policy(), observations=rows,
                                        contexts=contexts, evaluation_as_of=AS_OF)
    rows, contexts = _observations(2)
    contexts.pop(rows[0].forecast_id)
    with pytest.raises(CalibrationError, match="exactly one"):
        evaluate_contextual_calibration(policy=_policy(), observations=rows,
                                        contexts=contexts, evaluation_as_of=AS_OF)


def test_context_groups_below_policy_floor_are_not_called_calibrated():
    rows, contexts = _observations(20)
    report = evaluate_contextual_calibration(policy=_policy(), observations=rows,
                                             contexts=contexts, evaluation_as_of=AS_OF)
    assert all(group["status"] == "UNCALIBRATED_LOW_SAMPLE" for group in report["group_reports"])
