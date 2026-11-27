"""Out-of-time probability calibration split by point-in-time regime and time of day."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime

from .probability_calibration import (
    CalibrationError,
    CalibrationPolicy,
    ForecastObservation,
    evaluate_probability_calibration,
)


@dataclass(frozen=True, slots=True)
class CalibrationContext:
    regime_bucket: str
    time_of_day_bucket: str
    available_at: datetime

    def __post_init__(self) -> None:
        if not self.regime_bucket.strip() or not self.time_of_day_bucket.strip():
            raise CalibrationError("regime and time-of-day buckets are required")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise CalibrationError("calibration context timestamp must be timezone-aware")


def evaluate_contextual_calibration(*, policy: CalibrationPolicy,
                                    observations: tuple[ForecastObservation, ...],
                                    contexts: dict[str, CalibrationContext],
                                    evaluation_as_of: datetime) -> dict[str, object]:
    """Produce separate reliability reports for every frozen regime/time-of-day pair.

    Context features must be available no later than each forecast. Under-sampled groups
    retain the base calibrator's explicit UNCALIBRATED_LOW_SAMPLE status.
    """
    ids = {row.forecast_id for row in observations}
    if not observations or set(contexts) != ids:
        raise CalibrationError("exactly one point-in-time context is required for every forecast")
    grouped: dict[tuple[str, str], list[ForecastObservation]] = {}
    for row in observations:
        context = contexts[row.forecast_id]
        if context.available_at > row.predicted_at:
            raise CalibrationError("regime/time-of-day context must be available by forecast time")
        grouped.setdefault((context.regime_bucket, context.time_of_day_bucket), []).append(
            replace(row, context_bucket=f"{context.regime_bucket}|{context.time_of_day_bucket}"))

    reports = []
    for (regime, time_bucket), rows in sorted(grouped.items()):
        report = evaluate_probability_calibration(
            policy=policy, observations=tuple(rows), evaluation_as_of=evaluation_as_of)
        bins_70 = [
            {"probability_bin": item.probability_bin, "sample_count": item.sample_count,
             "mean_predicted_probability": item.mean_predicted_probability,
             "observed_frequency": item.observed_frequency,
             "observed_frequency_wilson_95": item.observed_frequency_wilson_95,
             "status": item.status}
            for item in report.reliability_bins
            if item.probability_bin == min(int(0.70 * policy.probability_bins),
                                           policy.probability_bins - 1)
        ]
        reports.append({"regime_bucket": regime, "time_of_day_bucket": time_bucket,
                        "sample_count": report.sample_count, "brier_score": report.brier_score,
                        "log_loss": report.log_loss, "status": report.status,
                        "reliability_bins": [
                            {"probability_bin": item.probability_bin,
                             "sample_count": item.sample_count,
                             "mean_predicted_probability": item.mean_predicted_probability,
                             "observed_frequency": item.observed_frequency,
                             "observed_frequency_wilson_95": item.observed_frequency_wilson_95,
                             "status": item.status}
                            for item in report.reliability_bins],
                        "70_percent_probability_bins": bins_70,
                        "evidence_sha256": report.evidence_sha256})
    body: dict[str, object] = {
        "calibration_id": policy.calibration_id,
        "policy_sha256": policy.digest(),
        "evaluation_as_of": evaluation_as_of.isoformat(),
        "out_of_time_only": True,
        "contexts_are_point_in_time": True,
        "group_reports": reports,
        "overall_status": "CALIBRATION_EVALUATED_NOT_PROMOTION",
        "execution_authorization": False,
    }
    body["evidence_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body
