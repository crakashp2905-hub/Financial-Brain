"""Nested chronological index-forecast splits with content-addressed frozen manifests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .index_15m import purged_time_splits


class IndexValidationError(ValueError):
    """Invalid temporal validation plan or manifest evidence."""


@dataclass(frozen=True)
class FrozenIndexManifest:
    canonical_json: str
    sha256: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IndexValidationError("all decision and outcome clocks must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise IndexValidationError(f"{name} must be lowercase SHA-256")


def build_nested_index_manifest(decision_times: list[datetime], label_end_times: list[datetime], *,
                                feature_names: list[str], hyperparameters: dict[str, Any],
                                source_data_sha256: str, outer_folds: int = 3,
                                inner_folds: int = 3,
                                embargo: timedelta = timedelta(minutes=15)
                                ) -> FrozenIndexManifest:
    """Freeze expanding train/validation/test folds and inner purged CV on training only.

    Repeated decision clocks remain indivisible. Labels that overlap a validation/test
    boundary after embargo are purged. The manifest binds source data, features, fixed
    hyperparameters and exact row membership before anyone evaluates the outer test.
    """
    _digest(source_data_sha256, "source data hash")
    if (len(decision_times) != len(label_end_times) or not decision_times
            or len(set(feature_names)) != len(feature_names)
            or any(not name for name in feature_names)):
        raise IndexValidationError("aligned decisions/labels and unique named features are required")
    if (type(outer_folds) is not int or type(inner_folds) is not int
            or outer_folds < 2 or inner_folds < 2 or embargo < timedelta(0)):
        raise IndexValidationError("need at least two outer/inner folds and nonnegative embargo")
    try:
        params_json = json.dumps(hyperparameters, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False)
    except (TypeError, ValueError) as error:
        raise IndexValidationError("hyperparameters must be finite JSON values") from error

    decisions = [_utc(value) for value in decision_times]
    label_ends = [_utc(value) for value in label_end_times]
    if decisions != sorted(decisions) or any(end < decision for decision, end in zip(decisions, label_ends)):
        raise IndexValidationError("decisions must be chronological and labels cannot precede decisions")
    unique = list(dict.fromkeys(decisions))
    if len(unique) < outer_folds + 2:
        raise IndexValidationError("not enough unique decision clocks for nested outer folds")

    val_size = max(1, len(unique) // (outer_folds * 4))
    outer: list[dict[str, Any]] = []
    for fold in range(outer_folds):
        test_start_pos = (len(unique) * (fold + 1)) // (outer_folds + 1)
        test_end_pos = (len(unique) * (fold + 2)) // (outer_folds + 1)
        val_start_pos = max(0, test_start_pos - val_size)
        if val_start_pos < inner_folds or test_end_pos <= test_start_pos:
            raise IndexValidationError("outer fold leaves insufficient train/validation/test history")
        val_start, test_start = unique[val_start_pos], unique[test_start_pos]
        validation = [i for i, decision in enumerate(decisions)
                      if val_start <= decision < test_start and label_ends[i] < test_start - embargo]
        train_boundary = val_start - embargo
        train = [i for i, (decision, end) in enumerate(zip(decisions, label_ends))
                 if decision < val_start and end < train_boundary]
        test_times = set(unique[test_start_pos:test_end_pos])
        test = [i for i, decision in enumerate(decisions) if decision in test_times]
        if not train or not validation or not test:
            raise IndexValidationError("purging emptied a train, validation or test partition")
        inner_decisions = [decisions[i] for i in train]
        inner_ends = [label_ends[i] for i in train]
        inner = purged_time_splits(inner_decisions, inner_ends, folds=inner_folds,
                                   embargo=embargo)
        if not inner:
            raise IndexValidationError("purging left no inner chronological validation folds")
        inner_manifest = [{"train": [train[index] for index in inner_train],
                           "validation": [train[index] for index in inner_validation]}
                          for inner_train, inner_validation in inner]
        outer.append({"fold": fold, "train": train, "validation": validation,
                      "test": test, "inner_folds": inner_manifest,
                      "validation_start": val_start.isoformat(),
                      "test_start": test_start.isoformat(),
                      "test_last_decision": unique[test_end_pos - 1].isoformat()})

    payload = {"schema": "fb.nested_index_validation_manifest.v1",
               "source_data_sha256": source_data_sha256,
               "feature_names": feature_names,
               "hyperparameters": json.loads(params_json),
               "hyperparameters_sha256": hashlib.sha256(params_json.encode()).hexdigest(),
               "decision_times_utc": [value.isoformat() for value in decisions],
               "label_end_times_utc": [value.isoformat() for value in label_ends],
               "outer_folds": outer, "embargo_seconds": int(embargo.total_seconds()),
               "test_policy": "outer test rows are evaluation only; inner folds and validation precede test"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return FrozenIndexManifest(canonical, hashlib.sha256(canonical.encode()).hexdigest())


def verify_nested_index_manifest(manifest: FrozenIndexManifest) -> bool:
    """Verify the content address of the immutable serialized manifest."""
    actual = hashlib.sha256(manifest.canonical_json.encode()).hexdigest()
    if actual != manifest.sha256:
        raise IndexValidationError("nested validation manifest hash mismatch")
    try:
        payload = json.loads(manifest.canonical_json)
    except json.JSONDecodeError as error:
        raise IndexValidationError("nested validation manifest JSON is invalid") from error
    if payload.get("schema") != "fb.nested_index_validation_manifest.v1":
        raise IndexValidationError("unsupported nested validation manifest schema")
    return True
