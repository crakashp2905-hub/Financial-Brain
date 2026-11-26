"""Temporal controls for prior-class and family-size evidence (ADR-0007 / PA-06-05).

An evaluation result cannot be certified as forward evidence unless its prior class and
family size were frozen before outcome access. Historical rescoring stays descriptive.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class PriorClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class EvidenceDisposition(StrEnum):
    FORWARD_REGISTERED = "FORWARD_REGISTERED"
    RETROSPECTIVE_ONLY = "RETROSPECTIVE_ONLY"


class FamilyRegistrationError(ValueError):
    """Malformed registration or invalid event chronology."""


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FamilyRegistrationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FamilyRegistration:
    registration_id: str
    hypothesis_id: str
    prior_class: PriorClass
    family_size: int
    registered_at: datetime
    preregistration_sha256: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (self.registration_id, self.hypothesis_id)):
            raise FamilyRegistrationError("registration and hypothesis IDs are required")
        if not isinstance(self.prior_class, PriorClass):
            raise FamilyRegistrationError("prior class must use the registered A/B/C enum")
        if type(self.family_size) is not int or self.family_size < 1:
            raise FamilyRegistrationError("family size must be a positive integer fixed at registration")
        _utc(self.registered_at, "registration clock")
        digest = self.preregistration_sha256
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise FamilyRegistrationError("preregistration must be identified by lowercase SHA-256")

    def identity(self) -> dict[str, object]:
        """Canonical, hashable registration identity for linking every scored result."""
        return {
            "registration_id": self.registration_id,
            "hypothesis_id": self.hypothesis_id,
            "prior_class": self.prior_class.value,
            "family_size": self.family_size,
            "registered_at": _utc(self.registered_at, "registration clock").isoformat(),
            "preregistration_sha256": self.preregistration_sha256,
        }

    def sha256(self) -> str:
        raw = json.dumps(self.identity(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FamilyEvaluation:
    registration: FamilyRegistration
    first_outcome_access_at: datetime
    scored_at: datetime
    result_sha256: str

    def __post_init__(self) -> None:
        access = _utc(self.first_outcome_access_at, "first outcome access clock")
        scored = _utc(self.scored_at, "score clock")
        if scored < access:
            raise FamilyRegistrationError("score cannot precede first outcome access")
        if len(self.result_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.result_sha256):
            raise FamilyRegistrationError("result must be identified by lowercase SHA-256")

    @property
    def disposition(self) -> EvidenceDisposition:
        registered = _utc(self.registration.registered_at, "registration clock")
        accessed = _utc(self.first_outcome_access_at, "first outcome access clock")
        return (EvidenceDisposition.FORWARD_REGISTERED if registered < accessed
                else EvidenceDisposition.RETROSPECTIVE_ONLY)

    @property
    def may_certify_forward(self) -> bool:
        return self.disposition is EvidenceDisposition.FORWARD_REGISTERED

    def audit_record(self) -> dict[str, object]:
        return {
            "registration": self.registration.identity(),
            "registration_sha256": self.registration.sha256(),
            "first_outcome_access_at": _utc(self.first_outcome_access_at, "first outcome access clock").isoformat(),
            "scored_at": _utc(self.scored_at, "score clock").isoformat(),
            "result_sha256": self.result_sha256,
            "disposition": self.disposition.value,
            "may_certify_forward": self.may_certify_forward,
        }


__all__ = [
    "EvidenceDisposition", "FamilyEvaluation", "FamilyRegistration", "FamilyRegistrationError", "PriorClass",
]
