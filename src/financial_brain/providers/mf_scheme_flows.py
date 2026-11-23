"""Source-backed mutual-fund distributions and unit/scheme transitions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST


class SchemeFlowError(ValueError):
    """Invalid, unauthorized, or ambiguous MF economic-flow evidence."""


class SchemeFlowKind(StrEnum):
    IDCW_PAYOUT = "IDCW_PAYOUT"
    IDCW_REINVESTMENT = "IDCW_REINVESTMENT"
    UNIT_SPLIT = "UNIT_SPLIT"
    SCHEME_MERGER = "SCHEME_MERGER"


def _decimal(value: Decimal | str | int, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SchemeFlowError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise SchemeFlowError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class SchemeEconomicFlow:
    scheme_code: str
    isin: str
    kind: SchemeFlowKind
    effective_date: date
    cash_per_unit: Decimal | str | int | None
    reinvestment_nav: Decimal | str | int | None
    unit_ratio: Decimal | str | int | None
    successor_scheme_code: str | None
    successor_isin: str | None
    source_id: str
    source_ref: str
    source_sha256: str
    published_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", SchemeFlowKind(self.kind))
        except ValueError as exc:
            raise SchemeFlowError("invalid scheme-flow kind") from exc
        for name in ("scheme_code", "isin", "source_id", "source_ref"):
            if not getattr(self, name).strip():
                raise SchemeFlowError(f"{name} is required")
        for name in ("cash_per_unit", "reinvestment_nav", "unit_ratio"):
            value = getattr(self, name)
            if value is not None:
                number = _decimal(value, name)
                if number < 0 or (name in ("reinvestment_nav", "unit_ratio") and number == 0):
                    raise SchemeFlowError(f"{name} must be {'positive' if name != 'cash_per_unit' else 'non-negative'}")
                object.__setattr__(self, name, number)
        required = {
            SchemeFlowKind.IDCW_PAYOUT: ("cash_per_unit",),
            SchemeFlowKind.IDCW_REINVESTMENT: ("cash_per_unit", "reinvestment_nav"),
            SchemeFlowKind.UNIT_SPLIT: ("unit_ratio",),
            SchemeFlowKind.SCHEME_MERGER: (
                "unit_ratio", "successor_scheme_code", "successor_isin"
            ),
        }[self.kind]
        for name in required:
            value = getattr(self, name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise SchemeFlowError(f"{name} is required for {self.kind.value}")
        if self.kind not in (SchemeFlowKind.IDCW_PAYOUT, SchemeFlowKind.IDCW_REINVESTMENT):
            if self.cash_per_unit is not None or self.reinvestment_nav is not None:
                raise SchemeFlowError("cash fields are invalid for unit/scheme transitions")
        if self.kind != SchemeFlowKind.SCHEME_MERGER:
            if self.successor_scheme_code is not None or self.successor_isin is not None:
                raise SchemeFlowError("successor is only valid for a scheme merger")
        for name in ("successor_scheme_code", "successor_isin"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise SchemeFlowError(f"{name} cannot be blank")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise SchemeFlowError("source_sha256 must be lowercase SHA-256")
        for name in ("published_at", "observed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise SchemeFlowError(f"{name} must be timezone-aware")

    def require_permissions(self, permissions: tuple[DataPermission, ...]) -> None:
        acquired_on = self.observed_at.astimezone(IST).date()
        for use in (DataUse.RESEARCH, DataUse.RETENTION):
            if not any(p.allows(source_id=self.source_id, use=use, on=acquired_on)
                       for p in permissions):
                raise SchemeFlowError(f"source {self.source_id} lacks effective {use.value} permission")

    @property
    def event_id(self) -> str:
        payload = {
            "scheme_code": self.scheme_code, "isin": self.isin, "kind": self.kind.value,
            "effective_date": self.effective_date.isoformat(),
            "cash_per_unit": str(self.cash_per_unit) if self.cash_per_unit is not None else None,
            "reinvestment_nav": str(self.reinvestment_nav) if self.reinvestment_nav is not None else None,
            "unit_ratio": str(self.unit_ratio) if self.unit_ratio is not None else None,
            "successor_scheme_code": self.successor_scheme_code,
            "successor_isin": self.successor_isin,
            "source_id": self.source_id, "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(body).hexdigest()


__all__ = ["SchemeEconomicFlow", "SchemeFlowError", "SchemeFlowKind"]
