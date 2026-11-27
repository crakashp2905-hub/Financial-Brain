import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_validation import (
    FrozenIndexManifest,
    IndexValidationError,
    build_nested_index_manifest,
    verify_nested_index_manifest,
)

IST = ZoneInfo("Asia/Kolkata")


def sample_manifest(*, params=None):
    start = datetime(2026, 1, 1, 9, 30, tzinfo=IST)
    decisions = [start + timedelta(minutes=15 * index) for index in range(120)]
    outcomes = [value + timedelta(minutes=15) for value in decisions]
    return build_nested_index_manifest(
        decisions, outcomes, feature_names=["ret_15m", "range"],
        hyperparameters=params or {"C": 1.0, "seed": 7},
        source_data_sha256="a" * 64, outer_folds=3, inner_folds=2,
        embargo=timedelta(minutes=15))


def test_nested_manifest_binds_outer_and_inner_chronological_purged_splits():
    manifest = sample_manifest()
    assert verify_nested_index_manifest(manifest)
    payload = json.loads(manifest.canonical_json)
    assert len(payload["outer_folds"]) == 3
    assert payload["hyperparameters"] == {"C": 1.0, "seed": 7}
    decisions = [datetime.fromisoformat(value) for value in payload["decision_times_utc"]]
    label_ends = [datetime.fromisoformat(value) for value in payload["label_end_times_utc"]]
    embargo = timedelta(seconds=payload["embargo_seconds"])
    for fold in payload["outer_folds"]:
        train, validation, test = fold["train"], fold["validation"], fold["test"]
        assert train and validation and test
        assert max(train) < min(validation) < min(test)
        assert all(label_ends[index] < decisions[min(validation)] - embargo for index in train)
        assert all(label_ends[index] < decisions[min(test)] - embargo for index in validation)
        assert fold["inner_folds"]
        for inner in fold["inner_folds"]:
            assert all(label_ends[index] < decisions[min(inner["validation"])] - embargo
                       for index in inner["train"])


def test_manifest_hash_changes_with_parameters_and_tampering_is_detected():
    first = sample_manifest()
    second = sample_manifest(params={"C": 0.5, "seed": 7})
    assert first.sha256 != second.sha256
    tampered = FrozenIndexManifest(first.canonical_json + " ", first.sha256)
    with pytest.raises(IndexValidationError, match="hash mismatch"):
        verify_nested_index_manifest(tampered)


def test_nested_manifest_rejects_naive_clocks_and_unserializable_parameters():
    start = datetime(2026, 1, 1, 9, 30, tzinfo=IST)
    decisions = [start + timedelta(minutes=15 * index) for index in range(20)]
    outcomes = [value + timedelta(minutes=15) for value in decisions]
    with pytest.raises(IndexValidationError, match="timezone-aware"):
        build_nested_index_manifest([value.replace(tzinfo=None) for value in decisions], outcomes,
                                    feature_names=["return"], hyperparameters={},
                                    source_data_sha256="a" * 64)
    with pytest.raises(IndexValidationError, match="finite JSON"):
        build_nested_index_manifest(decisions, outcomes, feature_names=["return"],
                                    hyperparameters={"value": float("nan")},
                                    source_data_sha256="a" * 64)
