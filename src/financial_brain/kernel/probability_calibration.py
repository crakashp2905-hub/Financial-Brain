"""Versioned out-of-time probability scoring with explicit low-sample reliability bins."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean

from .learning_tools import brier_score


class CalibrationError(ValueError):
    """Calibration evidence is invalid, leaked, incomplete, or out of scope."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CalibrationPolicy:
    calibration_id: str
    version: str
    model_sha256: str
    training_end_at: datetime
    holdout_start_at: datetime
    holdout_end_at: datetime
    minimum_observations_per_bucket: int = 30
    probability_bins: int = 10
    epsilon: float = 1e-15

    def __post_init__(self) -> None:
        if not self.calibration_id.strip() or not self.version.strip():
            raise CalibrationError("calibration identity and version are required")
        if not _SHA256.fullmatch(self.model_sha256):
            raise CalibrationError("model_sha256 must be lowercase SHA-256")
        for name in ("training_end_at", "holdout_start_at", "holdout_end_at"):
            _aware(name, getattr(self, name))
        if not (self.training_end_at < self.holdout_start_at < self.holdout_end_at):
            raise CalibrationError("training and holdout intervals must be strictly chronological")
        if type(self.minimum_observations_per_bucket) is not int or self.minimum_observations_per_bucket < 30:
            raise CalibrationError("minimum_observations_per_bucket must be at least 30")
        if type(self.probability_bins) is not int or not 2 <= self.probability_bins <= 100:
            raise CalibrationError("probability_bins must be between 2 and 100")
        if not math.isfinite(self.epsilon) or not 0 < self.epsilon < 0.01:
            raise CalibrationError("epsilon must be finite and in (0, 0.01)")

    def digest(self) -> str:
        body = {"calibration_id": self.calibration_id, "version": self.version,
                "model_sha256": self.model_sha256,
                "training_end_at": self.training_end_at.astimezone(timezone.utc).isoformat(),
                "holdout_start_at": self.holdout_start_at.astimezone(timezone.utc).isoformat(),
                "holdout_end_at": self.holdout_end_at.astimezone(timezone.utc).isoformat(),
                "minimum_observations_per_bucket": self.minimum_observations_per_bucket,
                "probability_bins": self.probability_bins, "epsilon": self.epsilon}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ForecastObservation:
    forecast_id: str
    probability: float
    outcome: int
    predicted_at: datetime
    outcome_available_at: datetime
    model_sha256: str
    observation_sha256: str
    context_bucket: str

    def __post_init__(self) -> None:
        if not self.forecast_id.strip() or not self.context_bucket.strip():
            raise CalibrationError("forecast_id and context_bucket are required")
        if not math.isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise CalibrationError("probability must be finite and in [0, 1]")
        if type(self.outcome) is not int or self.outcome not in {0, 1}:
            raise CalibrationError("binary outcome must be integer 0 or 1")
        _aware("predicted_at", self.predicted_at)
        _aware("outcome_available_at", self.outcome_available_at)
        for name in ("model_sha256", "observation_sha256"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise CalibrationError(f"{name} must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    context_bucket: str
    probability_bin: int
    sample_count: int
    mean_predicted_probability: float
    observed_frequency: float
    observed_frequency_wilson_95: tuple[float, float]
    status: str


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    calibration_id: str
    version: str
    policy_sha256: str
    model_sha256: str
    evaluation_as_of: datetime
    sample_count: int
    brier_score: float
    log_loss: float
    reliability_bins: tuple[ReliabilityBin, ...]
    status: str
    evidence_sha256: str
    is_execution_authorization: bool = False


def _wilson(successes: int, count: int) -> tuple[float, float]:
    z = 1.96
    p = successes / count
    denom = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denom
    margin = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def evaluate_probability_calibration(*, policy: CalibrationPolicy,
                                     observations: tuple[ForecastObservation, ...],
                                     evaluation_as_of: datetime) -> CalibrationReport:
    """Score frozen forecasts only after matured outcomes arrive, preserving OOT lineage.

    ``OUT_OF_TIME_EVALUATED`` is a score report, not a claim that calibration passed.
    Any reliability bucket below the frozen sample minimum remains explicitly uncalibrated.
    """
    if not isinstance(policy, CalibrationPolicy) or not observations:
        raise CalibrationError("a frozen policy and non-empty forecast observations are required")
    _aware("evaluation_as_of", evaluation_as_of)
    as_of = evaluation_as_of.astimezone(timezone.utc)
    start = policy.holdout_start_at.astimezone(timezone.utc)
    end = policy.holdout_end_at.astimezone(timezone.utc)
    train_end = policy.training_end_at.astimezone(timezone.utc)
    ids = [row.forecast_id for row in observations]
    if len(ids) != len(set(ids)):
        raise CalibrationError("forecast IDs must be unique")
    grouped: dict[tuple[str, int], list[ForecastObservation]] = {}
    for row in observations:
        predicted = row.predicted_at.astimezone(timezone.utc)
        outcome_at = row.outcome_available_at.astimezone(timezone.utc)
        if row.model_sha256 != policy.model_sha256:
            raise CalibrationError("forecast model hash differs from frozen calibration policy")
        if predicted <= train_end or not start <= predicted < end:
            raise CalibrationError("forecast is outside the strictly out-of-time holdout window")
        if outcome_at <= predicted or outcome_at > as_of:
            raise CalibrationError("outcome was not matured and available by evaluation time")
        bucket = min(int(row.probability * policy.probability_bins), policy.probability_bins - 1)
        grouped.setdefault((row.context_bucket, bucket), []).append(row)

    n = len(observations)
    brier = brier_score([row.probability for row in observations],
                        [row.outcome for row in observations])
    log_loss = fmean(-(
        row.outcome * math.log(min(1 - policy.epsilon, max(policy.epsilon, row.probability))) +
        (1 - row.outcome) * math.log(1 - min(1 - policy.epsilon, max(policy.epsilon, row.probability)))
    ) for row in observations)
    bins: list[ReliabilityBin] = []
    for (context_bucket, probability_bin), rows in sorted(grouped.items()):
        count = len(rows)
        wins = sum(row.outcome for row in rows)
        lower, upper = _wilson(wins, count)
        bins.append(ReliabilityBin(
            context_bucket, probability_bin, count,
            fmean(row.probability for row in rows), wins / count, (lower, upper),
            "OUT_OF_TIME_BUCKET_SAMPLE_SUFFICIENT" if count >= policy.minimum_observations_per_bucket
            else "UNCALIBRATED_LOW_SAMPLE",
        ))
    status = ("OUT_OF_TIME_EVALUATED" if n >= policy.minimum_observations_per_bucket
              else "UNCALIBRATED_LOW_SAMPLE")
    body = {
        "policy_sha256": policy.digest(), "evaluation_as_of": as_of.isoformat(),
        "observation_sha256": sorted(row.observation_sha256 for row in observations),
        "forecast_ids": sorted(ids), "brier_score": brier, "log_loss": log_loss,
        "bins": [{"context_bucket": b.context_bucket, "probability_bin": b.probability_bin,
                  "sample_count": b.sample_count, "mean_probability": b.mean_predicted_probability,
                  "observed_frequency": b.observed_frequency, "status": b.status} for b in bins],
        "status": status,
    }
    evidence_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                              allow_nan=False).encode()).hexdigest()
    return CalibrationReport(policy.calibration_id, policy.version, policy.digest(),
                             policy.model_sha256, as_of, n, brier, log_loss, tuple(bins),
                             status, evidence_hash)


__all__ = ["CalibrationError", "CalibrationPolicy", "CalibrationReport", "ForecastObservation",
           "ReliabilityBin", "evaluate_probability_calibration"]
