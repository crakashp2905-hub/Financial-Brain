"""Freeze multiplicity/dependence inference design before the first outcome read."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class InferenceRegistrationError(ValueError):
    """Invalid or retrospectively selected inference plan."""


class InferenceMethod(StrEnum):
    PBO_CSCV = "PBO_CSCV"
    DEFLATED_SHARPE = "DEFLATED_SHARPE"
    WHITE_REALITY_CHECK = "WHITE_REALITY_CHECK"
    HANSEN_SPA = "HANSEN_SPA"
    HOLM = "HOLM"
    SIDAK = "SIDAK"
    HAC_BLOCK_BOOTSTRAP = "HAC_BLOCK_BOOTSTRAP"


class InferenceDisposition(StrEnum):
    PRE_REGISTERED = "PRE_REGISTERED"
    RETROSPECTIVE_DIAGNOSTIC_ONLY = "RETROSPECTIVE_DIAGNOSTIC_ONLY"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InferenceRegistrationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class InferencePlan:
    plan_id: str
    method: InferenceMethod
    candidate_ids: tuple[str, ...]
    alpha: float
    registered_at: datetime
    preregistration_sha256: str
    block_length: float | None = None
    bootstrap_resamples: int | None = None

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not isinstance(self.method, InferenceMethod):
            raise InferenceRegistrationError("plan ID and closed inference method are required")
        if not self.candidate_ids or any(not isinstance(x, str) or not x.strip() for x in self.candidate_ids):
            raise InferenceRegistrationError("candidate family must be explicit and nonempty")
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or tuple(sorted(self.candidate_ids)) != self.candidate_ids:
            raise InferenceRegistrationError("candidate IDs must be unique and frozen in sorted order")
        if self.method in {InferenceMethod.PBO_CSCV, InferenceMethod.WHITE_REALITY_CHECK, InferenceMethod.HANSEN_SPA} \
                and len(self.candidate_ids) < 2:
            raise InferenceRegistrationError("family-level inference requires at least two candidates")
        if not math.isfinite(self.alpha) or not 0 < self.alpha < 0.5:
            raise InferenceRegistrationError("alpha must be fixed in (0, 0.5)")
        _utc(self.registered_at, "registration timestamp")
        digest = self.preregistration_sha256
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise InferenceRegistrationError("preregistration must use lowercase SHA-256")
        needs_block = self.method in {InferenceMethod.WHITE_REALITY_CHECK, InferenceMethod.HANSEN_SPA,
                                      InferenceMethod.HAC_BLOCK_BOOTSTRAP}
        if needs_block and (self.block_length is None or not math.isfinite(self.block_length)
                            or self.block_length < 1):
            raise InferenceRegistrationError("selected bootstrap method needs a positive fixed block length")
        if not needs_block and self.block_length is not None:
            raise InferenceRegistrationError("block length is not applicable to the selected method")
        if self.bootstrap_resamples is not None and (
            type(self.bootstrap_resamples) is not int or self.bootstrap_resamples < 200
        ):
            raise InferenceRegistrationError("bootstrap resamples must be at least 200")
        if needs_block and self.bootstrap_resamples is None:
            raise InferenceRegistrationError("bootstrap replicate count must be fixed before outcome access")

    def sha256(self) -> str:
        payload = {"plan_id": self.plan_id, "method": self.method.value,
                   "candidate_ids": list(self.candidate_ids), "alpha": self.alpha,
                   "registered_at": _utc(self.registered_at, "registration timestamp").isoformat(),
                   "preregistration_sha256": self.preregistration_sha256,
                   "block_length": self.block_length, "bootstrap_resamples": self.bootstrap_resamples}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class InferenceUse:
    plan: InferencePlan
    first_outcome_access_at: datetime
    scored_at: datetime

    def __post_init__(self) -> None:
        access = _utc(self.first_outcome_access_at, "first outcome access timestamp")
        scored = _utc(self.scored_at, "score timestamp")
        if scored < access:
            raise InferenceRegistrationError("score cannot precede first outcome access")

    @property
    def disposition(self) -> InferenceDisposition:
        registered = _utc(self.plan.registered_at, "registration timestamp")
        access = _utc(self.first_outcome_access_at, "first outcome access timestamp")
        return (InferenceDisposition.PRE_REGISTERED if registered < access
                else InferenceDisposition.RETROSPECTIVE_DIAGNOSTIC_ONLY)

    @property
    def may_certify_forward(self) -> bool:
        return self.disposition is InferenceDisposition.PRE_REGISTERED


__all__ = ["InferenceDisposition", "InferenceMethod", "InferencePlan", "InferenceRegistrationError", "InferenceUse"]
