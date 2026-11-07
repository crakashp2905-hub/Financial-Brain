"""Immutable pattern contracts: specs, events, lifecycle states and evidence tiers.

Contract summary (checklist CP-0027..CP-0040):
- ``PatternSpec`` canonicalises to identical bytes and SHA-256 on every platform.
- ``PatternEvent`` carries explicit clocks; ``candidate_known_at`` is never later
  than ``confirmed_at`` or ``earliest_entry_at``.
- Lifecycle transitions are enforced; invalid transitions raise
  :class:`InvalidTransition`.
- Detectors can only emit ``DESCRIPTIVE`` evidence. A shape cannot promote itself.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class InvalidTransition(ValueError):
    """Raised for a lifecycle transition the state machine does not allow."""


class InvalidContract(ValueError):
    """Raised when a spec or event fails strict validation (fail closed)."""


class EventState(str, Enum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


_TRANSITIONS: dict[EventState, frozenset[EventState]] = {
    EventState.CANDIDATE: frozenset(
        {EventState.CONFIRMED, EventState.INVALIDATED, EventState.EXPIRED, EventState.SUPERSEDED}
    ),
    EventState.CONFIRMED: frozenset({EventState.INVALIDATED, EventState.EXPIRED, EventState.SUPERSEDED}),
    EventState.INVALIDATED: frozenset(),
    EventState.EXPIRED: frozenset(),
    EventState.SUPERSEDED: frozenset(),
}


def check_transition(src: EventState, dst: EventState) -> None:
    """Raise :class:`InvalidTransition` unless ``src -> dst`` is allowed."""
    if dst not in _TRANSITIONS[src]:
        raise InvalidTransition(f"{src.value} -> {dst.value} is not allowed")


class EvidenceTier(str, Enum):
    """Ordered evidence tiers. Detector output is always DESCRIPTIVE."""

    DESCRIPTIVE = "DESCRIPTIVE"
    HISTORICAL_REPLICATED = "HISTORICAL_REPLICATED"
    FORWARD_PAPER = "FORWARD_PAPER"
    EXTERNALLY_CONFIRMED = "EXTERNALLY_CONFIRMED"


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON. Decimals become strings; NaN/inf are rejected."""

    def norm(value: Any) -> Any:
        if isinstance(value, bool) or value is None or isinstance(value, str | int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise InvalidContract("non-finite float in canonical payload")
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise InvalidContract("non-finite Decimal in canonical payload")
            return str(value)
        if isinstance(value, dict):
            return {str(k): norm(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [norm(v) for v in value]
        if isinstance(value, Enum):
            return value.value
        raise InvalidContract(f"unsupported type in canonical payload: {type(value).__name__}")

    return json.dumps(
        norm(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_aware(label: str, text: str) -> datetime:
    try:
        ts = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise InvalidContract(f"{label}: not an ISO-8601 timestamp: {text!r}") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise InvalidContract(f"{label}: timestamp must be timezone-aware: {text!r}")
    return ts


def _finite_positive(label: str, value: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise InvalidContract(f"{label}: must be a finite number, got {value!r}")
    if value <= 0:
        raise InvalidContract(f"{label}: must be positive, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class PatternSpec:
    pattern_id: str
    version: str
    timeframe: str
    parameters: dict[str, Any]
    recognition_rule: str
    confirmation_rule: str
    invalidation_rule: str
    source_id: str

    def __post_init__(self) -> None:
        for name in ("pattern_id", "version", "timeframe", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidContract(f"PatternSpec.{name} must be a non-empty string")
        # Freeze parameters by copying and round-tripping through canonical JSON.
        frozen = json.loads(canonical_json(self.parameters).decode("utf-8"))
        object.__setattr__(self, "parameters", frozen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "version": self.version,
            "timeframe": self.timeframe,
            "parameters": self.parameters,
            "recognition_rule": self.recognition_rule,
            "confirmation_rule": self.confirmation_rule,
            "invalidation_rule": self.invalidation_rule,
            "source_id": self.source_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatternSpec:
        return cls(**data)

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_dict())

    @property
    def sha256(self) -> str:
        return sha256_hex(self.canonical_bytes())


@dataclass(frozen=True)
class PatternEvent:
    event_id: str
    spec_sha256: str
    lineage: str
    timeframe: str
    candidate_known_at_utc: str
    status: EventState
    data_sha256: str
    confirmed_at_utc: str | None = None
    invalidated_at_utc: str | None = None
    earliest_entry_at_utc: str | None = None
    geometry: dict[str, Any] = field(default_factory=dict)
    evidence_tier: EvidenceTier = EvidenceTier.DESCRIPTIVE
    decision_at_utc: str | None = None
    formation_start_utc: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, EventState):
            object.__setattr__(self, "status", EventState(self.status))
        if not isinstance(self.evidence_tier, EvidenceTier):
            object.__setattr__(self, "evidence_tier", EvidenceTier(self.evidence_tier))
        if self.evidence_tier is not EvidenceTier.DESCRIPTIVE:
            raise InvalidContract("detector events must carry DESCRIPTIVE evidence only")
        known = _parse_aware("candidate_known_at_utc", self.candidate_known_at_utc)
        for label, value in (
            ("confirmed_at_utc", self.confirmed_at_utc),
            ("invalidated_at_utc", self.invalidated_at_utc),
            ("earliest_entry_at_utc", self.earliest_entry_at_utc),
        ):
            if value is None:
                continue
            ts = _parse_aware(label, value)
            if ts < known:
                raise InvalidContract(f"{label} precedes candidate_known_at_utc")
        if self.status is EventState.CONFIRMED and self.confirmed_at_utc is None:
            raise InvalidContract("CONFIRMED event requires confirmed_at_utc")
        if self.status is EventState.INVALIDATED and self.invalidated_at_utc is None:
            raise InvalidContract("INVALIDATED event requires invalidated_at_utc")
        if self.confirmed_at_utc and self.earliest_entry_at_utc:
            if _parse_aware("earliest_entry_at_utc", self.earliest_entry_at_utc) < _parse_aware(
                "confirmed_at_utc", self.confirmed_at_utc
            ):
                raise InvalidContract("earliest entry cannot precede confirmation")
        if self.formation_start_utc is not None:
            start = _parse_aware("formation_start_utc", self.formation_start_utc)
            if start > known:
                raise InvalidContract("formation_start_utc follows candidate_known_at_utc")
        if self.decision_at_utc is not None:
            decision = _parse_aware("decision_at_utc", self.decision_at_utc)
            if decision < known:
                raise InvalidContract("decision_at_utc precedes candidate_known_at_utc")
            if self.confirmed_at_utc and decision < _parse_aware("confirmed_at_utc", self.confirmed_at_utc):
                raise InvalidContract("decision cannot precede confirmation")
            if self.earliest_entry_at_utc and decision > _parse_aware(
                "earliest_entry_at_utc", self.earliest_entry_at_utc
            ):
                raise InvalidContract("decision cannot follow earliest entry")
        canonical_json(self.geometry)  # rejects NaN/inf and unsupported types

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "spec_sha256": self.spec_sha256,
            "lineage": self.lineage,
            "timeframe": self.timeframe,
            "candidate_known_at_utc": self.candidate_known_at_utc,
            "confirmed_at_utc": self.confirmed_at_utc,
            "invalidated_at_utc": self.invalidated_at_utc,
            "earliest_entry_at_utc": self.earliest_entry_at_utc,
            "status": self.status.value,
            "geometry": self.geometry,
            "data_sha256": self.data_sha256,
            "evidence_tier": self.evidence_tier.value,
            "decision_at_utc": self.decision_at_utc,
            "formation_start_utc": self.formation_start_utc,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatternEvent:
        return cls(**data)

    def payload_sha256(self) -> str:
        return sha256_hex(canonical_json(self.to_dict()))


@dataclass(frozen=True)
class PatternSignal:
    """Research observation derived from an event (CP-0039).

    Deliberately has no side, quantity, price or account fields, so it cannot be
    converted into an order intent. ``research_only`` is fixed to True.
    """

    event_id: str
    label: str
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.research_only is not True:
            raise InvalidContract("PatternSignal is research-only")
        if not self.event_id or not self.label:
            raise InvalidContract("PatternSignal requires event_id and label")


@dataclass(frozen=True)
class Provenance:
    """Inputs that reproduce a result exactly (CP-0035, CP-0037)."""

    catalogue_sha256: str
    code_commit: str
    calendar_id: str
    config_sha256: str

    @staticmethod
    def config_hash(parameters: dict[str, Any]) -> str:
        return sha256_hex(canonical_json(parameters))

    def to_dict(self) -> dict[str, str]:
        return {
            "catalogue_sha256": self.catalogue_sha256,
            "code_commit": self.code_commit,
            "calendar_id": self.calendar_id,
            "config_sha256": self.config_sha256,
        }

    def sha256(self) -> str:
        return sha256_hex(canonical_json(self.to_dict()))


def make_event_id(
    *, spec_sha256: str, lineage: str, timeframe: str, candidate_known_at_utc: str
) -> str:
    """Stable identity from lineage, timeframe, spec and first-known time (CP-0034)."""
    _parse_aware("candidate_known_at_utc", candidate_known_at_utc)
    material = canonical_json(
        {
            "spec_sha256": spec_sha256,
            "lineage": lineage,
            "timeframe": timeframe,
            "candidate_known_at_utc": candidate_known_at_utc,
        }
    )
    return "pev_" + sha256_hex(material)[:32]


def transition(event: PatternEvent, to: EventState, **changes: Any) -> PatternEvent:
    """Return a new event in state ``to``; the original is never mutated."""
    check_transition(event.status, to)
    data = event.to_dict()
    data.update(changes)
    data["status"] = to.value
    return PatternEvent.from_dict(data)
