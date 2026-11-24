from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.probability_calibration import (
    CalibrationError,
    CalibrationPolicy,
    ForecastObservation,
    evaluate_probability_calibration,
)


HASH = "a" * 64
TRAIN_END = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2026, 11, 1, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2026, 11, 30, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 24, tzinfo=timezone.utc)


def _policy(**changes):
    values = dict(calibration_id="swing-direction", version="v1", model_sha256=HASH,
                  training_end_at=TRAIN_END, holdout_start_at=HOLDOUT_START,
                  holdout_end_at=HOLDOUT_END, minimum_observations_per_bucket=30,
                  probability_bins=10)
    return CalibrationPolicy(**(values | changes))


def _row(index, *, probability=0.7, outcome=1, context="NSE_OPEN"):
    predicted = HOLDOUT_START + timedelta(minutes=index + 1)
    return ForecastObservation(
        forecast_id=f"forecast-{index}", probability=probability, outcome=outcome,
        predicted_at=predicted, outcome_available_at=predicted + timedelta(hours=1),
        model_sha256=HASH, observation_sha256=f"{index + 1:064x}", context_bucket=context,
    )


def test_out_of_time_report_scores_brier_logloss_and_wilson_reliability_bins():
    rows = tuple(_row(i, outcome=1 if i < 20 else 0) for i in range(30))

    report = evaluate_probability_calibration(policy=_policy(), observations=rows,
                                              evaluation_as_of=AS_OF)

    assert report.sample_count == 30
    assert report.brier_score == pytest.approx((20 * 0.09 + 10 * 0.49) / 30)
    assert report.log_loss > 0
    assert report.status == "OUT_OF_TIME_EVALUATED"
    assert report.reliability_bins[0].probability_bin == 7
    assert report.reliability_bins[0].sample_count == 30
    assert report.reliability_bins[0].status == "OUT_OF_TIME_BUCKET_SAMPLE_SUFFICIENT"
    lo, hi = report.reliability_bins[0].observed_frequency_wilson_95
    assert lo < 20 / 30 < hi
    assert not report.is_execution_authorization
    assert len(report.policy_sha256) == len(report.evidence_sha256) == 64


def test_low_sample_context_bucket_is_explicitly_uncalibrated():
    rows = tuple(_row(i, context="LOW_COVERAGE_REGIME") for i in range(3))

    report = evaluate_probability_calibration(policy=_policy(), observations=rows,
                                              evaluation_as_of=AS_OF)

    assert report.status == "UNCALIBRATED_LOW_SAMPLE"
    assert report.reliability_bins[0].status == "UNCALIBRATED_LOW_SAMPLE"
    assert report.reliability_bins[0].sample_count == 3


def test_training_overlap_future_maturity_and_model_mismatch_are_rejected():
    bad_rows = (
        _row(0, context="valid"),
        ForecastObservation("train-overlap", 0.5, 1, TRAIN_END,
                            TRAIN_END + timedelta(days=1), HASH, "b" * 64, "bad"),
    )
    with pytest.raises(CalibrationError, match="out-of-time"):
        evaluate_probability_calibration(policy=_policy(), observations=bad_rows,
                                         evaluation_as_of=AS_OF)
    with pytest.raises(CalibrationError, match="matured"):
        evaluate_probability_calibration(
            policy=_policy(), observations=(ForecastObservation(
                "future-outcome", 0.5, 1, HOLDOUT_START, AS_OF + timedelta(seconds=1),
                HASH, "b" * 64, "bad"),), evaluation_as_of=AS_OF)
    with pytest.raises(CalibrationError, match="model hash"):
        evaluate_probability_calibration(
            policy=_policy(), observations=(ForecastObservation(
                "wrong-model", 0.5, 1, HOLDOUT_START, HOLDOUT_START + timedelta(days=1),
                "b" * 64, "c" * 64, "bad"),), evaluation_as_of=AS_OF)


def test_boundary_probabilities_have_finite_clipped_log_loss_and_duplicate_ids_fail():
    rows = (
        _row(0, probability=0.0, outcome=0),
        _row(1, probability=1.0, outcome=1),
    )
    report = evaluate_probability_calibration(policy=_policy(), observations=rows,
                                              evaluation_as_of=AS_OF)
    assert report.log_loss < 1e-12
    with pytest.raises(CalibrationError, match="unique"):
        evaluate_probability_calibration(policy=_policy(), observations=(rows[0], rows[0]),
                                         evaluation_as_of=AS_OF)


def test_policy_requires_chronological_holdout_and_minimum_sample_floor():
    with pytest.raises(CalibrationError, match="chronological"):
        _policy(holdout_start_at=TRAIN_END)
    with pytest.raises(CalibrationError, match="at least 30"):
        _policy(minimum_observations_per_bucket=10)
