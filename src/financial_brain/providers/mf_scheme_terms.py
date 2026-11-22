"""Effective-dated, source-backed MF costs and dealing constraints."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST


class SchemeTermsError(ValueError):
    """Invalid or unauthorized scheme cost and dealing-term evidence."""


class ExitRestriction(StrEnum):
    ELSS_LOCK_IN = "ELSS_LOCK_IN"
    SEGREGATED_PORTFOLIO = "SEGREGATED_PORTFOLIO"
    GATING = "GATING"
    CLOSED_TO_REDEMPTION = "CLOSED_TO_REDEMPTION"
    CLOSED_ENDED_WINDOW = "CLOSED_ENDED_WINDOW"
    OTHER = "OTHER"


@dataclass(frozen=True)
class ExitLoadBand:
    """One holding-period threshold and the disclosed exit load in percent."""

    days_from_purchase: int
    rate_percent: Decimal

    def __post_init__(self) -> None:
        if self.days_from_purchase < 0:
            raise SchemeTermsError("days_from_purchase cannot be negative")
        object.__setattr__(self, "rate_percent", _decimal(self.rate_percent, "exit-load rate"))
        if not Decimal(0) <= self.rate_percent <= Decimal(100):
            raise SchemeTermsError("exit-load rate must be between 0 and 100 percent")


def _decimal(value: Decimal | str | int, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SchemeTermsError(f"{label} must be an exact decimal value") from exc
    if not result.is_finite():
        raise SchemeTermsError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class SchemeCommercialTerms:
    """One version of scheme costs, load tiers and redemption restrictions.

    ``None`` means the source did not establish a field; an empty tuple means the
    source explicitly states that no exit load or restriction applies.
    """

    scheme_code: str
    plan: str
    option: str
    isin: str
    effective_from: date
    effective_to: date | None
    ter_percent: Decimal | str | int | None
    aum_inr: Decimal | str | int | None
    exit_loads: tuple[ExitLoadBand, ...] | None
    minimum_initial_inr: Decimal | str | int | None
    minimum_additional_inr: Decimal | str | int | None
    minimum_redemption_inr: Decimal | str | int | None
    exit_restrictions: tuple[ExitRestriction, ...] | None
    source_id: str
    source_ref: str
    source_sha256: str
    published_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("scheme_code", "plan", "option", "isin", "source_id", "source_ref"):
            if not getattr(self, name).strip():
                raise SchemeTermsError(f"{name} is required")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise SchemeTermsError("effective_to precedes effective_from")
        for field_name in ("ter_percent", "aum_inr", "minimum_initial_inr",
                           "minimum_additional_inr", "minimum_redemption_inr"):
            value = getattr(self, field_name)
            if value is not None:
                number = _decimal(value, field_name)
                if number < 0 or (field_name == "ter_percent" and number >= 100):
                    raise SchemeTermsError(f"{field_name} is outside its valid range")
                object.__setattr__(self, field_name, number)
        if self.exit_loads is not None:
            days = [band.days_from_purchase for band in self.exit_loads]
            if days != sorted(set(days)):
                raise SchemeTermsError("exit-load thresholds must be strictly increasing")
        if self.exit_restrictions is not None:
            try:
                object.__setattr__(self, "exit_restrictions", tuple(
                    ExitRestriction(value) for value in self.exit_restrictions
                ))
            except ValueError as exc:
                raise SchemeTermsError("invalid exit restriction") from exc
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise SchemeTermsError("source_sha256 must be lowercase SHA-256")
        for name in ("published_at", "observed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise SchemeTermsError(f"{name} must be timezone-aware")

    def require_permissions(self, permissions: tuple[DataPermission, ...]) -> None:
        acquired_on = self.observed_at.astimezone(IST).date()
        for use in (DataUse.RESEARCH, DataUse.RETENTION):
            if not any(p.allows(source_id=self.source_id, use=use, on=acquired_on)
                       for p in permissions):
                raise SchemeTermsError(
                    f"source {self.source_id} lacks effective {use.value} permission"
                )

    @property
    def record_id(self) -> str:
        def decimal_text(value: Decimal | str | int | None) -> str | None:
            return str(_decimal(value, "decimal field")) if value is not None else None

        payload = {
            "identity": [self.scheme_code, self.plan, self.option, self.isin],
            "effective": [self.effective_from.isoformat(),
                          self.effective_to.isoformat() if self.effective_to else None],
            "ter_percent": decimal_text(self.ter_percent),
            "aum_inr": decimal_text(self.aum_inr),
            "exit_loads": ([{"days": x.days_from_purchase,
                             "rate_percent": str(x.rate_percent)} for x in self.exit_loads]
                           if self.exit_loads is not None else None),
            "minimum_initial_inr": decimal_text(self.minimum_initial_inr),
            "minimum_additional_inr": decimal_text(self.minimum_additional_inr),
            "minimum_redemption_inr": decimal_text(self.minimum_redemption_inr),
            "exit_restrictions": ([x.value for x in self.exit_restrictions]
                                  if self.exit_restrictions is not None else None),
            "source_id": self.source_id, "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(body).hexdigest()


__all__ = ["ExitLoadBand", "ExitRestriction", "SchemeCommercialTerms", "SchemeTermsError"]
