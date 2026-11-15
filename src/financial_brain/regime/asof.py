"""Point-in-time regime snapshots that exclude observations unavailable at decision time."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Sequence

from .brain import VERSION, indicate


class AsOfRegimeError(ValueError):
    """Invalid or unverifiable regime feature evidence."""


FEATURES = (
    "c", "ma50", "ma200", "k200", "ma200_slope", "dd", "ret20", "rv20", "vix",
    "above_200",
)
CORE_FEATURES = frozenset({"c", "ma50", "ma200", "k200"})


@dataclass(frozen=True)
class TimedFeature:
    field: str
    value: float | int | None
    event_utc: datetime
    available_utc: datetime
    source_sha256: str


@dataclass(frozen=True)
class AsOfRegime:
    status: str
    as_of_utc: datetime
    classifier_version: str
    regime: str | None
    reasons: tuple[str, ...]
    missing_features: tuple[str, ...]
    selected_inputs: tuple[tuple[str, float | int | None, str], ...]
    evidence_sha256: str


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AsOfRegimeError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _validate_feature(point: TimedFeature) -> tuple[datetime, datetime]:
    if point.field not in FEATURES:
        raise AsOfRegimeError(f"unknown regime feature: {point.field}")
    event = _utc(point.event_utc, "event_utc")
    available = _utc(point.available_utc, "available_utc")
    if event > available:
        raise AsOfRegimeError("feature availability cannot precede its event time")
    if not isinstance(point.source_sha256, str) or len(point.source_sha256) != 64:
        raise AsOfRegimeError("source_sha256 must be a SHA-256 hex digest")
    try:
        int(point.source_sha256, 16)
    except ValueError as exc:
        raise AsOfRegimeError("source_sha256 must be a SHA-256 hex digest") from exc
    if point.value is not None:
        if type(point.value) not in {int, float} or not isfinite(point.value):
            raise AsOfRegimeError("feature value must be a finite number or explicit null")
    return event, available


def classify_asof(points: Sequence[TimedFeature], *, decision_utc: datetime) -> AsOfRegime:
    """Classify from the latest event for each field both observed and available by cutoff.

    Features stamped at the signal time but received later are excluded. A daily close or
    full-session regime stamped after a morning signal is therefore unusable. Missing
    core history fails closed; optional classifier inputs may be explicitly null.
    """
    cutoff = _utc(decision_utc, "decision_utc")
    latest: dict[str, tuple[datetime, datetime, TimedFeature]] = {}
    for point in points:
        event, available = _validate_feature(point)
        if event > cutoff or available > cutoff:
            continue
        current = latest.get(point.field)
        if current is None or (event, available) > (current[0], current[1]):
            latest[point.field] = (event, available, point)

    missing = tuple(sorted(set(FEATURES) - latest.keys()))
    selected = tuple(
        (field, latest[field][2].value, latest[field][2].source_sha256)
        for field in FEATURES if field in latest
    )
    values = {field: latest[field][2].value for field in latest}
    missing_core = CORE_FEATURES - values.keys()
    core_incomplete = bool(missing_core) or any(values.get(name) is None for name in CORE_FEATURES)
    core_incomplete = core_incomplete or type(values.get("k200")) is not int
    if core_incomplete:
        state, reasons = None, ("INSUFFICIENT_AS_OF_CORE_FEATURES",)
    else:
        state, reasons = indicate({field: values.get(field) for field in FEATURES})

    digest_body = {
        "decision_utc": cutoff.isoformat(),
        "classifier_version": VERSION,
        "status": "AS_OF" if state is not None else "BLOCKED_MISSING_AS_OF_FEATURES",
        "regime": state,
        "reasons": list(reasons),
        "missing_features": list(missing),
        "selected_inputs": [list(item) for item in selected],
    }
    digest = hashlib.sha256(json.dumps(
        digest_body, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return AsOfRegime(
        status=digest_body["status"],
        as_of_utc=cutoff,
        classifier_version=VERSION,
        regime=state,
        reasons=tuple(reasons),
        missing_features=missing,
        selected_inputs=selected,
        evidence_sha256=digest,
    )
