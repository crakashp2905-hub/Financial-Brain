"""Source-bound, effective-dated mutual-fund scheme reference metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST


class SchemeMasterError(ValueError):
    """Invalid or unauthorized mutual-fund master record."""


class PlanType(StrEnum):
    DIRECT = "DIRECT"
    REGULAR = "REGULAR"
    UNKNOWN = "UNKNOWN"


class OptionType(StrEnum):
    GROWTH = "GROWTH"
    IDCW_PAYOUT = "IDCW_PAYOUT"
    IDCW_REINVESTMENT = "IDCW_REINVESTMENT"
    UNKNOWN = "UNKNOWN"


class SchemeStructure(StrEnum):
    OPEN_ENDED = "OPEN_ENDED"
    CLOSED_ENDED = "CLOSED_ENDED"
    INTERVAL = "INTERVAL"
    UNKNOWN = "UNKNOWN"


class InvestmentStyle(StrEnum):
    ACTIVE = "ACTIVE"
    PASSIVE = "PASSIVE"
    UNKNOWN = "UNKNOWN"


class AssetCategory(StrEnum):
    EQUITY = "EQUITY"
    DEBT = "DEBT"
    HYBRID = "HYBRID"
    COMMODITY = "COMMODITY"
    FUND_OF_FUNDS = "FUND_OF_FUNDS"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SchemeMasterRecord:
    """One source-backed version of a scheme's plan, option and classification."""

    scheme_code: str
    plan: PlanType
    option: OptionType
    isin: str
    effective_from: date
    effective_to: date | None
    structure: SchemeStructure
    investment_style: InvestmentStyle
    asset_category: AssetCategory
    category_label: str
    fund_house: str | None
    riskometer: str | None
    benchmark_tri: str | None
    manager: str | None
    objective: str | None
    source_id: str
    source_ref: str
    source_sha256: str
    published_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        for name, enum_type in (
            ("plan", PlanType), ("option", OptionType),
            ("structure", SchemeStructure), ("investment_style", InvestmentStyle),
            ("asset_category", AssetCategory),
        ):
            try:
                object.__setattr__(self, name, enum_type(getattr(self, name)))
            except ValueError as exc:
                raise SchemeMasterError(f"invalid {name}") from exc
        if not self.scheme_code.strip() or not self.isin.strip():
            raise SchemeMasterError("scheme_code and ISIN are required in the canonical key")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise SchemeMasterError("effective_to precedes effective_from")
        if not self.source_id.strip() or not self.source_ref.strip():
            raise SchemeMasterError("source identity and reference are required")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise SchemeMasterError("source_sha256 must be lowercase SHA-256")
        for name in ("published_at", "observed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise SchemeMasterError(f"{name} must be timezone-aware")

    @property
    def identity(self) -> tuple[str, PlanType, OptionType, str]:
        return self.scheme_code, self.plan, self.option, self.isin

    def require_permissions(self, permissions: tuple[DataPermission, ...]) -> None:
        """Require separate source rights for research and durable retention."""
        acquired_on = self.observed_at.astimezone(IST).date()
        for use in (DataUse.RESEARCH, DataUse.RETENTION):
            if not any(p.allows(source_id=self.source_id, use=use, on=acquired_on)
                       for p in permissions):
                raise SchemeMasterError(f"source {self.source_id} lacks effective {use.value} permission")


__all__ = [
    "AssetCategory", "InvestmentStyle", "OptionType", "PlanType", "SchemeMasterError",
    "SchemeMasterRecord", "SchemeStructure",
]
