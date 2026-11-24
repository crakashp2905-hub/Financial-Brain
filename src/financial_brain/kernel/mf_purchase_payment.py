"""Separate, point-in-time payment milestones for paper MF purchases.

Payment initiation, credit to a scheme, and availability to utilize funds are distinct
events. Missing events remain missing; no milestone is inferred from another.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class MFPurchasePaymentError(ValueError):
    """Invalid or causally inconsistent MF purchase payment evidence."""


class PaymentMilestoneKind(StrEnum):
    INITIATED = "INITIATED"
    CREDITED_TO_SCHEME = "CREDITED_TO_SCHEME"
    AVAILABLE_FOR_UTILIZATION = "AVAILABLE_FOR_UTILIZATION"


class PaymentEvidenceBasis(StrEnum):
    SIMULATED_ASSUMPTION = "SIMULATED_ASSUMPTION"
    OBSERVED_EVIDENCE = "OBSERVED_EVIDENCE"


@dataclass(frozen=True, slots=True)
class MFPurchasePaymentMilestone:
    """One independently observed or simulated payment event."""

    kind: PaymentMilestoneKind
    effective_at: datetime
    received_at: datetime
    basis: PaymentEvidenceBasis
    source_id: str | None = None
    source_ref: str | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", PaymentMilestoneKind(self.kind))
            basis = PaymentEvidenceBasis(self.basis)
        except ValueError as exc:
            raise MFPurchasePaymentError("unknown milestone or evidence basis") from exc
        object.__setattr__(self, "basis", basis)
        for name in ("effective_at", "received_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise MFPurchasePaymentError(f"{name} must be timezone-aware")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.received_at < self.effective_at:
            raise MFPurchasePaymentError("milestone cannot be received before it is effective")
        provenance = (self.source_id, self.source_ref, self.source_sha256)
        if basis is PaymentEvidenceBasis.OBSERVED_EVIDENCE:
            if not all(provenance):
                raise MFPurchasePaymentError("observed milestone requires source provenance")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_sha256 or ""):
                raise MFPurchasePaymentError("source_sha256 must be a SHA-256 digest")
        elif any(provenance):
            raise MFPurchasePaymentError("simulated milestone cannot claim external evidence")


@dataclass(frozen=True, slots=True)
class MFPurchasePaymentTimeline:
    """Immutable set of known milestones for one purchase order."""

    order_id: str
    milestones: tuple[MFPurchasePaymentMilestone, ...] = ()

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise MFPurchasePaymentError("order_id is required")
        milestones = tuple(self.milestones)
        if any(not isinstance(item, MFPurchasePaymentMilestone) for item in milestones):
            raise MFPurchasePaymentError("milestones must be typed payment events")
        kinds = [item.kind for item in milestones]
        if len(kinds) != len(set(kinds)):
            raise MFPurchasePaymentError("each payment milestone may be recorded only once")
        # Enforce causal order only when both adjacent milestones are known. A missing
        # timestamp stays unknown and never gets copied or calculated from another one.
        by_kind = {item.kind: item for item in milestones}
        sequence = (PaymentMilestoneKind.INITIATED,
                    PaymentMilestoneKind.CREDITED_TO_SCHEME,
                    PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION)
        for earlier, later in zip(sequence, sequence[1:]):
            if (earlier in by_kind and later in by_kind
                    and by_kind[earlier].effective_at > by_kind[later].effective_at):
                raise MFPurchasePaymentError(
                    f"{earlier.value} cannot occur after {later.value}"
                )
        object.__setattr__(self, "milestones", milestones)

    def at(self, kind: PaymentMilestoneKind) -> MFPurchasePaymentMilestone | None:
        """Return only a directly recorded event; never synthesize its timestamp."""
        target = PaymentMilestoneKind(kind)
        return next((item for item in self.milestones if item.kind is target), None)

    @property
    def event_sha256(self) -> str:
        payload = {
            "order_id": self.order_id,
            "milestones": [
                {"kind": item.kind.value, "effective_at": item.effective_at.isoformat(),
                 "received_at": item.received_at.isoformat(), "basis": item.basis.value,
                 "source_id": item.source_id, "source_ref": item.source_ref,
                 "source_sha256": item.source_sha256}
                for item in sorted(self.milestones, key=lambda entry: entry.kind.value)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "MFPurchasePaymentError", "MFPurchasePaymentMilestone", "MFPurchasePaymentTimeline",
    "PaymentEvidenceBasis", "PaymentMilestoneKind",
]
