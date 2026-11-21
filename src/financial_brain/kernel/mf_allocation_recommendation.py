"""Mutual-fund allocation guidance that does not imply instant execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID


class MFRecommendationError(ValueError):
    """Invalid mutual-fund allocation recommendation or NAV availability state."""


class MFAction(StrEnum):
    ALLOCATE = "ALLOCATE"
    ADD = "ADD"
    REDUCE = "REDUCE"
    REDEEM = "REDEEM"
    SWITCH = "SWITCH"
    SIP = "SIP"
    HOLD = "HOLD"


class NAVStatus(StrEnum):
    CONFIRMED_PUBLISHED = "CONFIRMED_PUBLISHED"
    UNCONFIRMED_PENDING = "UNCONFIRMED_PENDING"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MFAllocationRecommendation:
    recommendation_id: str
    scheme_code: str
    plan: str
    option: str
    action: MFAction
    decision_at: datetime
    effective_date: date
    cutoff_at: datetime
    amount_inr: Decimal
    lot_fee_estimate_inr: Decimal
    benchmark_id: str
    nav_status: NAVStatus
    nav_as_of: date | None
    nav_published_at: datetime | None
    nav_available_at: datetime | None
    horizon_sessions: int
    rationale: str

    def __post_init__(self) -> None:
        try:
            UUID(self.recommendation_id)
        except (ValueError, AttributeError) as exc:
            raise MFRecommendationError("recommendation_id must be a UUID") from exc
        for name in ("scheme_code", "plan", "option", "benchmark_id", "rationale"):
            if not getattr(self, name).strip():
                raise MFRecommendationError(f"{name} is required")
        if not isinstance(self.action, MFAction) or not isinstance(self.nav_status, NAVStatus):
            raise MFRecommendationError("action and nav_status must use their typed enums")
        for name in ("decision_at", "cutoff_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise MFRecommendationError(f"{name} must be timezone-aware")
        if self.cutoff_at.astimezone(timezone.utc) <= self.decision_at.astimezone(timezone.utc):
            raise MFRecommendationError("cutoff_at must be after the recommendation decision")
        for name in ("amount_inr", "lot_fee_estimate_inr"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise MFRecommendationError(f"{name} must be decimal") from exc
            if not value.is_finite() or (value < 0 if name == "lot_fee_estimate_inr" else value <= 0):
                raise MFRecommendationError(f"{name} is outside its valid range")
            object.__setattr__(self, name, value)
        if self.horizon_sessions < 1:
            raise MFRecommendationError("horizon_sessions must be positive")
        for name in ("nav_published_at", "nav_available_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise MFRecommendationError(f"{name} must be timezone-aware when present")
        if self.nav_status is NAVStatus.CONFIRMED_PUBLISHED:
            if self.nav_as_of is None or self.nav_published_at is None or self.nav_available_at is None:
                raise MFRecommendationError("confirmed NAV requires date, publication and availability clocks")
            if self.nav_available_at.astimezone(timezone.utc) > self.decision_at.astimezone(timezone.utc):
                raise MFRecommendationError("future NAV availability cannot be used for this decision")
        elif self.nav_status is NAVStatus.UNCONFIRMED_PENDING:
            if self.nav_as_of is not None or self.nav_published_at is not None or self.nav_available_at is not None:
                raise MFRecommendationError("unconfirmed NAV cannot include fabricated publication clocks")

    @property
    def is_instant_execution(self) -> bool:
        """MF recommendations are allocation guidance; settlement and NAV occur later."""
        return False


__all__ = ["MFAction", "MFAllocationRecommendation", "MFRecommendationError", "NAVStatus"]
