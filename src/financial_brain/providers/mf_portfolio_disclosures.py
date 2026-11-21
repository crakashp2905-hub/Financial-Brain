"""Permissioned point-in-time rows from MF portfolio disclosures."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST


class MfDisclosureError(ValueError):
    """Invalid or unauthorized mutual-fund portfolio disclosure row."""


class PortfolioAssetClass(StrEnum):
    EQUITY = "EQUITY"
    DEBT = "DEBT"
    CASH = "CASH"
    COMMODITY = "COMMODITY"
    FUND_OF_FUNDS = "FUND_OF_FUNDS"
    REIT_INVIT = "REIT_INVIT"
    DERIVATIVE = "DERIVATIVE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MfDisclosureError(f"{name} must be decimal") from exc
    if not result.is_finite():
        raise MfDisclosureError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class PortfolioDisclosureRow:
    scheme_code: str
    plan: str
    option: str
    scheme_isin: str
    disclosure_date: date
    asset_class: PortfolioAssetClass
    weight_percent: Decimal | str | int
    asset_id: str | None
    asset_name: str | None
    sector: str | None
    credit_rating: str | None
    modified_duration_years: Decimal | str | int | None
    yield_to_maturity_percent: Decimal | str | int | None
    source_id: str
    source_ref: str
    source_sha256: str
    published_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "asset_class", PortfolioAssetClass(self.asset_class))
        except ValueError as exc:
            raise MfDisclosureError("invalid asset_class") from exc
        for name in ("scheme_code", "plan", "option", "scheme_isin", "source_id", "source_ref"):
            if not getattr(self, name).strip():
                raise MfDisclosureError(f"{name} is required")
        weight = _decimal(self.weight_percent, "weight_percent")
        if not Decimal(0) <= weight <= Decimal(100):
            raise MfDisclosureError("weight_percent must be between 0 and 100")
        object.__setattr__(self, "weight_percent", weight)
        for name in ("modified_duration_years", "yield_to_maturity_percent"):
            value = getattr(self, name)
            if value is not None:
                number = _decimal(value, name)
                if number < 0 or (name == "yield_to_maturity_percent" and number >= 100):
                    raise MfDisclosureError(f"{name} is outside its valid range")
                object.__setattr__(self, name, number)
        if self.asset_class != PortfolioAssetClass.DEBT and any(
            value is not None for value in (
                self.credit_rating, self.modified_duration_years,
                self.yield_to_maturity_percent,
            )
        ):
            raise MfDisclosureError("debt-only fields require asset_class=DEBT")
        if self.asset_id is not None and not self.asset_id.strip():
            raise MfDisclosureError("asset_id cannot be blank")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise MfDisclosureError("source_sha256 must be lowercase SHA-256")
        for name in ("published_at", "observed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise MfDisclosureError(f"{name} must be timezone-aware")

    def require_permissions(self, permissions: tuple[DataPermission, ...]) -> None:
        acquired_on = self.observed_at.astimezone(IST).date()
        for use in (DataUse.RESEARCH, DataUse.RETENTION):
            if not any(p.allows(source_id=self.source_id, use=use, on=acquired_on)
                       for p in permissions):
                raise MfDisclosureError(f"source {self.source_id} lacks effective {use.value} permission")

    @property
    def record_id(self) -> str:
        values = {
            "scheme": [self.scheme_code, self.plan, self.option, self.scheme_isin],
            "disclosure_date": self.disclosure_date.isoformat(),
            "asset_class": self.asset_class.value, "weight_percent": str(self.weight_percent),
            "asset_id": self.asset_id, "asset_name": self.asset_name, "sector": self.sector,
            "credit_rating": self.credit_rating,
            "modified_duration_years": (str(self.modified_duration_years)
                                         if self.modified_duration_years is not None else None),
            "yield_to_maturity_percent": (str(self.yield_to_maturity_percent)
                                          if self.yield_to_maturity_percent is not None else None),
            "source_id": self.source_id, "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
        }
        body = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(body).hexdigest()


__all__ = ["MfDisclosureError", "PortfolioAssetClass", "PortfolioDisclosureRow"]
