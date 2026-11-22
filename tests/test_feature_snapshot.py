from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.features.snapshot import FeatureSnapshot, FeatureSnapshotError

UTC = timezone.utc
CUTOFF = datetime(2026, 11, 20, 10, tzinfo=UTC)
SOURCE = "a" * 64


def _snapshot(**overrides) -> FeatureSnapshot:
    args = {
        "entity_id": "NSE:INE000000019",
        "session": date(2026, 11, 20),
        "feature_version": "f2",
        "as_of": CUTOFF,
        "values": {"momentum_20": 0.12, "sector_event": None},
        "observed_at": {"momentum_20": CUTOFF - timedelta(minutes=1), "sector_event": None},
        "available_at": {"momentum_20": CUTOFF, "sector_event": None},
        "source_hashes": {"prices": SOURCE},
        "feature_sources": {"momentum_20": ("prices",), "sector_event": ()},
    }
    args.update(overrides)
    return FeatureSnapshot.create(**args)


def test_snapshot_tracks_clocks_hashes_missingness_and_round_trips() -> None:
    snapshot = _snapshot()
    payload = snapshot.to_dict()

    assert snapshot.missingness_mask == {"momentum_20": False, "sector_event": True}
    assert payload["source_hashes"] == {"prices": SOURCE}
    assert payload["available_at"]["momentum_20"] == CUTOFF.isoformat()
    assert payload["snapshot_sha256"] == snapshot.snapshot_sha256
    assert len(snapshot.snapshot_sha256) == 64
    assert FeatureSnapshot.from_dict(payload).snapshot_sha256 == snapshot.snapshot_sha256


def test_snapshot_hash_is_order_independent_and_changes_with_lineage() -> None:
    first = _snapshot()
    reordered = _snapshot(
        values={"sector_event": None, "momentum_20": 0.12},
        observed_at={"sector_event": None, "momentum_20": CUTOFF - timedelta(minutes=1)},
        available_at={"sector_event": None, "momentum_20": CUTOFF},
        feature_sources={"sector_event": (), "momentum_20": ("prices",)},
    )
    changed_source = _snapshot(source_hashes={"prices": "b" * 64})

    assert first.snapshot_sha256 == reordered.snapshot_sha256
    assert first.snapshot_sha256 != changed_source.snapshot_sha256


def test_future_or_unavailable_feature_is_rejected() -> None:
    with pytest.raises(FeatureSnapshotError, match="unavailable at snapshot cutoff"):
        _snapshot(available_at={"momentum_20": CUTOFF + timedelta(seconds=1), "sector_event": None})


def test_naive_clock_and_observation_after_availability_are_rejected() -> None:
    with pytest.raises(FeatureSnapshotError, match="timezone-aware"):
        _snapshot(as_of=datetime(2026, 11, 20, 10))
    with pytest.raises(FeatureSnapshotError, match="before it was observed"):
        _snapshot(observed_at={"momentum_20": CUTOFF + timedelta(seconds=1), "sector_event": None})


def test_missing_values_are_not_silently_given_timestamps_or_sources() -> None:
    with pytest.raises(FeatureSnapshotError, match="cannot carry observation clocks"):
        _snapshot(observed_at={"momentum_20": CUTOFF - timedelta(minutes=1), "sector_event": CUTOFF})
    with pytest.raises(FeatureSnapshotError, match="needs a source reference"):
        _snapshot(feature_sources={"momentum_20": (), "sector_event": ()})


def test_malformed_or_unresolved_source_hashes_are_rejected() -> None:
    with pytest.raises(FeatureSnapshotError, match="lowercase SHA-256"):
        _snapshot(source_hashes={"prices": "not-a-hash"})
    with pytest.raises(FeatureSnapshotError, match="must resolve"):
        _snapshot(source_hashes={}, feature_sources={"momentum_20": ("missing",), "sector_event": ()})


def test_serialized_snapshot_rejects_tampering() -> None:
    payload = _snapshot().to_dict()
    payload["values"]["momentum_20"] = 0.99

    with pytest.raises(FeatureSnapshotError, match="hash does not match"):
        FeatureSnapshot.from_dict(payload)


def test_explicit_freshness_budgets_reject_stale_indicator_values() -> None:
    old_observation = CUTOFF - timedelta(days=3)
    snapshot = _snapshot(
        observed_at={"momentum_20": old_observation, "sector_event": None},
        available_at={"momentum_20": old_observation, "sector_event": None},
    )

    with pytest.raises(FeatureSnapshotError, match="exceeds its freshness budget"):
        snapshot.validate_freshness(max_observation_age={
            "momentum_20": timedelta(days=1), "sector_event": timedelta(days=30),
        })
    snapshot.validate_freshness(max_observation_age={
        "momentum_20": timedelta(days=5), "sector_event": timedelta(days=30),
    })
