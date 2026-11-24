"""Conservative, evidence-labelled MF paper execution realism score."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class MFRealismError(ValueError):
    """Invalid MF paper realism assessment."""


class MFRealismDimension(StrEnum):
    ELIGIBLE_NAV_CERTAINTY = "ELIGIBLE_NAV_CERTAINTY"
    PAYMENT_TIME_EVIDENCE = "PAYMENT_TIME_EVIDENCE"
    LOAD_COMPLETENESS = "LOAD_COMPLETENESS"
    REDEMPTION_SETTLEMENT_CONFIDENCE = "REDEMPTION_SETTLEMENT_CONFIDENCE"


class MFRealismBasis(StrEnum):
    SIMULATED_ASSUMPTION = "SIMULATED_ASSUMPTION"
    OBSERVED_EVIDENCE = "OBSERVED_EVIDENCE"


WEIGHTS: dict[MFRealismDimension, Decimal] = {
    MFRealismDimension.ELIGIBLE_NAV_CERTAINTY: Decimal("0.35"),
    MFRealismDimension.PAYMENT_TIME_EVIDENCE: Decimal("0.25"),
    MFRealismDimension.LOAD_COMPLETENESS: Decimal("0.20"),
    MFRealismDimension.REDEMPTION_SETTLEMENT_CONFIDENCE: Decimal("0.20"),
}


@dataclass(frozen=True, slots=True)
class MFRealismFactor:
    """One 0..1 evidence-backed or explicitly assumed dimension rating."""

    dimension: MFRealismDimension
    value: Decimal
    basis: MFRealismBasis
    assessed_at: datetime
    rationale: str
    evidence_ref: str | None = None
    source_id: str | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "dimension", MFRealismDimension(self.dimension))
            object.__setattr__(self, "basis", MFRealismBasis(self.basis))
            value = Decimal(self.value)
        except (ValueError, InvalidOperation, TypeError) as exc:
            raise MFRealismError("factor dimension, basis, or decimal value is invalid") from exc
        if not value.is_finite() or value < 0 or value > 1:
            raise MFRealismError("factor value must be in [0, 1]")
        object.__setattr__(self, "value", value)
        if self.assessed_at.tzinfo is None or self.assessed_at.utcoffset() is None:
            raise MFRealismError("assessed_at must be timezone-aware")
        object.__setattr__(self, "assessed_at", self.assessed_at.astimezone(timezone.utc))
        if not self.rationale.strip():
            raise MFRealismError("factor rationale is required")
        provenance = (self.evidence_ref, self.source_id, self.source_sha256)
        if self.basis is MFRealismBasis.OBSERVED_EVIDENCE:
            if not all(provenance):
                raise MFRealismError("observed factor requires evidence reference and source identity/hash")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_sha256 or ""):
                raise MFRealismError("source_sha256 must be a SHA-256 digest")
        elif any(provenance):
            raise MFRealismError("simulated factor cannot claim observed source evidence")


@dataclass(frozen=True, slots=True)
class MFPaperRealismAssessment:
    """Conservative score: missing dimensions contribute zero and reduce coverage."""

    account_id: str
    assessed_at: datetime
    factors: tuple[MFRealismFactor, ...]

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise MFRealismError("account_id is required")
        if self.assessed_at.tzinfo is None or self.assessed_at.utcoffset() is None:
            raise MFRealismError("assessment timestamp must be timezone-aware")
        object.__setattr__(self, "assessed_at", self.assessed_at.astimezone(timezone.utc))
        factors = tuple(self.factors)
        if any(not isinstance(item, MFRealismFactor) for item in factors):
            raise MFRealismError("factors must be typed MFRealismFactor records")
        dimensions = [item.dimension for item in factors]
        if len(dimensions) != len(set(dimensions)):
            raise MFRealismError("each realism dimension may be assessed only once")
        if any(item.assessed_at > self.assessed_at for item in factors):
            raise MFRealismError("assessment cannot include future-dated factor evidence")
        object.__setattr__(self, "factors", factors)

    @property
    def score(self) -> Decimal:
        """0..100 score with absent dimensions assigned zero, never silently excluded."""
        values = {factor.dimension: factor.value for factor in self.factors}
        return sum((WEIGHTS[dim] * values.get(dim, Decimal(0)) for dim in WEIGHTS),
                   Decimal(0)) * 100

    @property
    def evidence_coverage(self) -> Decimal:
        """0..100 weighted share of dimensions with any recorded assessment."""
        present = {factor.dimension for factor in self.factors}
        return sum((weight for dimension, weight in WEIGHTS.items() if dimension in present),
                   Decimal(0)) * 100

    @property
    def observed_coverage(self) -> Decimal:
        """0..100 weighted share backed by source-attested observations."""
        observed = {factor.dimension for factor in self.factors
                    if factor.basis is MFRealismBasis.OBSERVED_EVIDENCE}
        return sum((weight for dimension, weight in WEIGHTS.items() if dimension in observed),
                   Decimal(0)) * 100

    @property
    def missing_dimensions(self) -> tuple[MFRealismDimension, ...]:
        present = {factor.dimension for factor in self.factors}
        return tuple(dimension for dimension in WEIGHTS if dimension not in present)

    @property
    def assessment_sha256(self) -> str:
        payload = {
            "account_id": self.account_id, "assessed_at": self.assessed_at.isoformat(),
            "factors": [
                {"dimension": factor.dimension.value, "value": str(factor.value),
                 "basis": factor.basis.value, "assessed_at": factor.assessed_at.isoformat(),
                 "rationale": factor.rationale, "evidence_ref": factor.evidence_ref,
                 "source_id": factor.source_id, "source_sha256": factor.source_sha256}
                for factor in sorted(self.factors, key=lambda item: item.dimension.value)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "MFPaperRealismAssessment", "MFRealismBasis", "MFRealismDimension",
    "MFRealismError", "MFRealismFactor", "WEIGHTS",
]
