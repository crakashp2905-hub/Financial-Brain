"""Provenance and calibration gates for experimental world-model artifacts.

The module records research-only rollouts and calibration eligibility. It does not
simulate markets or allow modeled trajectories to qualify a strategy for promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorldModelError(ValueError):
    """Invalid world-model provenance or calibration evidence."""


@dataclass(frozen=True, slots=True)
class WorldModelRolloutEvidence:
    rollout_id: str
    model_sha256: str
    conditioning_snapshot_sha256: str
    generated_at_utc: datetime
    horizon: int
    path_count: int
    uncertainty_by_horizon: tuple[float, ...]
    evidence_status: str = "WORLD_MODEL_ONLY"
    promotion_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.rollout_id.strip():
            raise WorldModelError("rollout identity is required")
        for label, value in (("model", self.model_sha256),
                             ("conditioning snapshot", self.conditioning_snapshot_sha256)):
            if not _SHA256.fullmatch(value):
                raise WorldModelError(f"{label} must be a lowercase SHA-256")
        if self.generated_at_utc.tzinfo is None or self.generated_at_utc.utcoffset() is None:
            raise WorldModelError("rollout timestamp must be timezone-aware")
        if self.horizon < 1 or self.path_count < 1 or len(self.uncertainty_by_horizon) != self.horizon:
            raise WorldModelError("positive horizon/path count and one uncertainty value per step are required")
        if any(not math.isfinite(x) or x < 0 for x in self.uncertainty_by_horizon):
            raise WorldModelError("horizon uncertainty values must be finite and non-negative")
        if self.evidence_status != "WORLD_MODEL_ONLY" or self.promotion_allowed:
            raise WorldModelError("modeled rollouts are permanently non-promotion evidence")
        object.__setattr__(self, "generated_at_utc", self.generated_at_utc.astimezone(timezone.utc))

    def manifest(self) -> dict[str, object]:
        body: dict[str, object] = {
            "rollout_id": self.rollout_id,
            "model_sha256": self.model_sha256,
            "conditioning_snapshot_sha256": self.conditioning_snapshot_sha256,
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "horizon": self.horizon,
            "path_count": self.path_count,
            "uncertainty_by_horizon": list(self.uncertainty_by_horizon),
            "evidence_status": self.evidence_status,
            "promotion_allowed": False,
        }
        body["manifest_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        return body


@dataclass(frozen=True, slots=True)
class CalibrationGate:
    status: str
    observations: int
    maximum_coverage_error: float | None
    pit_uniformity_pvalue: float | None
    reasons: tuple[str, ...]
    promotion_allowed: bool = False


def distributional_calibration_gate(
    *,
    nominal_coverage: Mapping[float, float],
    observed_coverage: Mapping[float, float],
    pit_uniformity_pvalue: float,
    observations: int,
    minimum_observations: int = 100,
    maximum_coverage_error: float = 0.10,
    minimum_pit_pvalue: float = 0.01,
) -> CalibrationGate:
    """Reject thin or materially miscalibrated forecasts without considering simulated P&L."""
    if not nominal_coverage or set(nominal_coverage) != set(observed_coverage):
        raise WorldModelError("nominal and observed coverage levels must be non-empty and aligned")
    if type(observations) is not int or observations < 0 or minimum_observations < 1:
        raise WorldModelError("observation counts must be valid")
    for name, value in (("PIT p-value", pit_uniformity_pvalue),
                        ("maximum coverage error", maximum_coverage_error),
                        ("minimum PIT p-value", minimum_pit_pvalue)):
        if not math.isfinite(value):
            raise WorldModelError(f"{name} must be finite")
    if not 0 <= pit_uniformity_pvalue <= 1 or not 0 <= minimum_pit_pvalue <= 1:
        raise WorldModelError("PIT p-values must be in [0, 1]")
    if not 0 <= maximum_coverage_error <= 1:
        raise WorldModelError("maximum coverage error must be in [0, 1]")
    errors = []
    for level in nominal_coverage:
        nominal, observed = nominal_coverage[level], observed_coverage[level]
        if not (0 < level < 1 and 0 < nominal < 1 and 0 <= observed <= 1):
            raise WorldModelError("coverage levels and values must be valid probabilities")
        errors.append(abs(nominal - observed))
    worst = max(errors)
    reasons = []
    if observations < minimum_observations:
        reasons.append("INSUFFICIENT_CALIBRATION_SAMPLE")
    if worst > maximum_coverage_error:
        reasons.append("COVERAGE_ERROR_EXCEEDS_LIMIT")
    if pit_uniformity_pvalue < minimum_pit_pvalue:
        reasons.append("PIT_UNIFORMITY_REJECTED")
    status = ("RETIRE" if any(reason != "INSUFFICIENT_CALIBRATION_SAMPLE" for reason in reasons)
              else "INSUFFICIENT_EVIDENCE" if reasons else "RESEARCH_ELIGIBLE")
    return CalibrationGate(status, observations,
                           worst, pit_uniformity_pvalue, tuple(reasons))


__all__ = ["CalibrationGate", "WorldModelError", "WorldModelRolloutEvidence",
           "distributional_calibration_gate"]
