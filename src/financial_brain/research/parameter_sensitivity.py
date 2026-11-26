"""Parameter-neighbour sensitivity with training-only selection and descriptive test reporting."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone


class ParameterSensitivityError(ValueError):
    """Invalid sensitivity plan or evidence chronology."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ParameterSensitivityError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sha(value: str, label: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ParameterSensitivityError(f"{label} must be lowercase SHA-256")


@dataclass(frozen=True)
class ParameterSensitivityPlan:
    plan_id: str
    parameter_name: str
    parameter_values: tuple[float, ...]
    registered_at: datetime
    first_test_access_at: datetime
    training_source_sha256: str
    validation_source_sha256: str
    test_source_sha256: str
    center_variant_id: str

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.parameter_name.strip() or not self.center_variant_id.strip():
            raise ParameterSensitivityError("plan, parameter and training-selected center IDs are required")
        if len(self.parameter_values) < 3 or any(not math.isfinite(x) for x in self.parameter_values):
            raise ParameterSensitivityError("sensitivity grid needs at least three finite parameter values")
        if tuple(sorted(set(self.parameter_values))) != self.parameter_values:
            raise ParameterSensitivityError("parameter grid values must be unique and increasing")
        if _utc(self.registered_at, "registration time") >= _utc(self.first_test_access_at, "first test access time"):
            raise ParameterSensitivityError("sensitivity plan must be frozen before test outcomes are first read")
        for value, label in ((self.training_source_sha256, "training source"),
                             (self.validation_source_sha256, "validation source"),
                             (self.test_source_sha256, "test source")):
            _sha(value, label)


@dataclass(frozen=True)
class ParameterVariantResult:
    variant_id: str
    parameter_value: float
    training_score: float
    validation_score: float
    test_score: float

    def __post_init__(self) -> None:
        if not self.variant_id.strip() or not all(math.isfinite(x) for x in
                                                  (self.parameter_value, self.training_score,
                                                   self.validation_score, self.test_score)):
            raise ParameterSensitivityError("variant ID and all finite scores are required")


def analyze_parameter_sensitivity(plan: ParameterSensitivityPlan,
                                  variants: tuple[ParameterVariantResult, ...], *,
                                  neighbor_radius: int = 1) -> dict[str, object]:
    """Choose the center only by training score; expose held-out scores without test ranking."""
    if type(neighbor_radius) is not int or neighbor_radius < 1:
        raise ParameterSensitivityError("neighbor radius must be a positive integer")
    if len(variants) != len(plan.parameter_values):
        raise ParameterSensitivityError("every frozen parameter value needs one result row")
    if len({row.variant_id for row in variants}) != len(variants):
        raise ParameterSensitivityError("variant IDs must be unique")
    ordered = tuple(sorted(variants, key=lambda row: row.parameter_value))
    if tuple(row.parameter_value for row in ordered) != plan.parameter_values:
        raise ParameterSensitivityError("result grid differs from the frozen sensitivity plan")
    training_best = max(ordered, key=lambda row: (row.training_score, row.variant_id))
    if training_best.variant_id != plan.center_variant_id:
        raise ParameterSensitivityError("center parameter must be selected by the frozen training score only")
    center_index = ordered.index(training_best)
    lo, hi = max(0, center_index - neighbor_radius), min(len(ordered), center_index + neighbor_radius + 1)
    neighbors = ordered[lo:hi]
    center = ordered[center_index]
    result_rows = [{"variant_id": row.variant_id, "parameter_value": row.parameter_value,
                    "training_score": row.training_score, "validation_score": row.validation_score,
                    "test_score_descriptive_only": row.test_score} for row in ordered]
    body: dict[str, object] = {
        "plan_id": plan.plan_id, "parameter_name": plan.parameter_name,
        "center_variant_id": center.variant_id, "selection_basis": "TRAINING_SCORE_ONLY",
        "test_period_used_for_selection": False,
        "neighbor_variant_ids": [row.variant_id for row in neighbors],
        "center_validation_score": center.validation_score,
        "neighbor_validation_scores": [row.validation_score for row in neighbors],
        "center_test_score_descriptive_only": center.test_score,
        "neighbor_test_scores_descriptive_only": [row.test_score for row in neighbors],
        "variants": result_rows,
        "training_source_sha256": plan.training_source_sha256,
        "validation_source_sha256": plan.validation_source_sha256,
        "test_source_sha256": plan.test_source_sha256,
    }
    body["report_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                                    allow_nan=False).encode()).hexdigest()
    return body


__all__ = ["ParameterSensitivityError", "ParameterSensitivityPlan", "ParameterVariantResult",
           "analyze_parameter_sensitivity"]
