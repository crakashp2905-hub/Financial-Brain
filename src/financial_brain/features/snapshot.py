"""Immutable, content-addressed point-in-time feature snapshots.

Each feature carries its own observation and availability clocks and references one or
more hashed source objects. Missing values are explicit in ``missingness_mask`` rather
than silently filled. A snapshot is admissible only when every populated feature was
available by its decision cutoff.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_Scalar = str | int | float | bool | None


class FeatureSnapshotError(ValueError):
    """Snapshot violates point-in-time or provenance requirements."""


def _aware(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureSnapshotError(f"{field} timestamps must be timezone-aware")
    return value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class FeatureSnapshot:
    """One entity/session feature vector with causal clocks and hashed lineage."""

    entity_id: str
    session: date
    feature_version: str
    as_of: datetime
    values: Mapping[str, _Scalar]
    observed_at: Mapping[str, datetime | None]
    available_at: Mapping[str, datetime | None]
    source_hashes: Mapping[str, str]
    feature_sources: Mapping[str, tuple[str, ...]]
    missingness_mask: Mapping[str, bool]
    snapshot_sha256: str

    @classmethod
    def create(
        cls,
        *,
        entity_id: str,
        session: date,
        feature_version: str,
        as_of: datetime,
        values: Mapping[str, _Scalar],
        observed_at: Mapping[str, datetime | None],
        available_at: Mapping[str, datetime | None],
        source_hashes: Mapping[str, str],
        feature_sources: Mapping[str, tuple[str, ...]],
    ) -> "FeatureSnapshot":
        """Validate inputs and build a deterministically hashed snapshot."""
        cutoff = _aware(as_of, "as_of")
        assert cutoff is not None
        if not entity_id.strip() or not feature_version.strip() or not values:
            raise FeatureSnapshotError("entity, version, and at least one feature are required")
        keys = set(values)
        if not all(isinstance(k, str) and k.strip() for k in keys):
            raise FeatureSnapshotError("feature names must be non-empty strings")
        if set(observed_at) != keys or set(available_at) != keys or set(feature_sources) != keys:
            raise FeatureSnapshotError("feature values, clocks, and source references must have identical keys")

        checked_values: dict[str, _Scalar] = {}
        checked_observed: dict[str, datetime | None] = {}
        checked_available: dict[str, datetime | None] = {}
        checked_sources: dict[str, tuple[str, ...]] = {}
        missing: dict[str, bool] = {}
        for name, raw_value in values.items():
            if raw_value is not None and not isinstance(raw_value, (str, int, float, bool)):
                raise FeatureSnapshotError(f"feature {name!r} must be a JSON scalar or null")
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                raise FeatureSnapshotError(f"feature {name!r} must be finite")
            obs = _aware(observed_at[name], f"observed_at[{name}]")
            avail = _aware(available_at[name], f"available_at[{name}]")
            refs = tuple(sorted(set(feature_sources[name])))
            is_missing = raw_value is None
            if is_missing:
                if obs is not None or avail is not None:
                    raise FeatureSnapshotError(f"missing feature {name!r} cannot carry observation clocks")
            else:
                if obs is None or avail is None:
                    raise FeatureSnapshotError(f"populated feature {name!r} needs observation and availability clocks")
                if obs > avail:
                    raise FeatureSnapshotError(f"feature {name!r} is available before it was observed")
                if avail > cutoff:
                    raise FeatureSnapshotError(f"feature {name!r} was unavailable at snapshot cutoff")
                if not refs:
                    raise FeatureSnapshotError(f"populated feature {name!r} needs a source reference")
            checked_values[name] = raw_value
            checked_observed[name] = obs
            checked_available[name] = avail
            checked_sources[name] = refs
            missing[name] = is_missing

        checked_hashes = dict(source_hashes)
        for source_id, digest in checked_hashes.items():
            if not source_id.strip() or not _SHA256.fullmatch(digest):
                raise FeatureSnapshotError("source hashes must map non-empty IDs to lowercase SHA-256 digests")
        referenced = {ref for refs in checked_sources.values() for ref in refs}
        if referenced - set(checked_hashes):
            raise FeatureSnapshotError("feature source references must resolve to source hashes")

        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "session": session.isoformat(),
            "feature_version": feature_version,
            "as_of": cutoff.isoformat(),
            "values": checked_values,
            "observed_at": {k: _iso(v) for k, v in checked_observed.items()},
            "available_at": {k: _iso(v) for k, v in checked_available.items()},
            "source_hashes": checked_hashes,
            "feature_sources": {k: list(v) for k, v in checked_sources.items()},
            "missingness_mask": missing,
        }
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return cls(
            entity_id=entity_id,
            session=session,
            feature_version=feature_version,
            as_of=cutoff,
            values=MappingProxyType(checked_values),
            observed_at=MappingProxyType(checked_observed),
            available_at=MappingProxyType(checked_available),
            source_hashes=MappingProxyType(checked_hashes),
            feature_sources=MappingProxyType(checked_sources),
            missingness_mask=MappingProxyType(missing),
            snapshot_sha256=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready snapshot, including its content hash."""
        return {
            "entity_id": self.entity_id,
            "session": self.session.isoformat(),
            "feature_version": self.feature_version,
            "as_of": self.as_of.isoformat(),
            "values": dict(self.values),
            "observed_at": {k: _iso(v) for k, v in self.observed_at.items()},
            "available_at": {k: _iso(v) for k, v in self.available_at.items()},
            "source_hashes": dict(self.source_hashes),
            "feature_sources": {k: list(v) for k, v in self.feature_sources.items()},
            "missingness_mask": dict(self.missingness_mask),
            "snapshot_sha256": self.snapshot_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FeatureSnapshot":
        """Revalidate a serialized snapshot and verify its content address."""
        try:
            source_hash = payload["snapshot_sha256"]
            snapshot = cls.create(
                entity_id=payload["entity_id"],
                session=date.fromisoformat(payload["session"]),
                feature_version=payload["feature_version"],
                as_of=datetime.fromisoformat(payload["as_of"]),
                values=payload["values"],
                observed_at={
                    key: datetime.fromisoformat(value) if value is not None else None
                    for key, value in payload["observed_at"].items()
                },
                available_at={
                    key: datetime.fromisoformat(value) if value is not None else None
                    for key, value in payload["available_at"].items()
                },
                source_hashes=payload["source_hashes"],
                feature_sources={key: tuple(value) for key, value in payload["feature_sources"].items()},
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, FeatureSnapshotError):
                raise
            raise FeatureSnapshotError("serialized feature snapshot is malformed") from exc
        if source_hash != snapshot.snapshot_sha256:
            raise FeatureSnapshotError("serialized feature snapshot hash does not match its contents")
        if payload.get("missingness_mask") != dict(snapshot.missingness_mask):
            raise FeatureSnapshotError("serialized missingness mask does not match feature values")
        return snapshot


__all__ = ["FeatureSnapshot", "FeatureSnapshotError"]
