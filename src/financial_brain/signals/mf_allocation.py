"""Daily/weekly MF allocation recommendations; never instant-execution orders."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class MFAction(StrEnum):
    ALLOCATE = "ALLOCATE"
    REDEEM = "REDEEM"
    HOLD = "HOLD"


class NavStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MFAllocationRecommendation:
    recommendation_id: str
    scheme_code: str
    action: MFAction
    amount_inr: float
    decision_at: datetime
    transaction_cutoff_at: datetime
    benchmark_id: str
    nav_status: NavStatus
    estimated_transaction_fee_inr: float | None
    estimated_exit_load_inr: float | None
    cost_evidence_status: str

    @property
    def workflow_type(self) -> str:
        return "ALLOCATION_RECOMMENDATION"

    def __post_init__(self) -> None:
        if not self.recommendation_id.strip() or not self.scheme_code.strip() or not self.benchmark_id.strip():
            raise ValueError("recommendation, scheme and benchmark identifiers are required")
        if self.amount_inr < 0 or not isfinite(self.amount_inr):
            raise ValueError("amount_inr must be finite and non-negative")
        if self.decision_at.tzinfo is None or self.transaction_cutoff_at.tzinfo is None:
            raise ValueError("decision and transaction cutoff timestamps must be timezone-aware")
        if self.transaction_cutoff_at < self.decision_at:
            raise ValueError("transaction cutoff cannot precede the recommendation")
        for value in (self.estimated_transaction_fee_inr, self.estimated_exit_load_inr):
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError("estimated fees must be finite and non-negative")
        if self.cost_evidence_status not in {"COMPLETE", "PARTIAL", "UNKNOWN"}:
            raise ValueError("cost_evidence_status must disclose coverage")

    @property
    def cost_estimate_complete(self) -> bool:
        return (self.cost_evidence_status == "COMPLETE"
                and self.estimated_transaction_fee_inr is not None
                and (self.action != MFAction.REDEEM or self.estimated_exit_load_inr is not None))
