"""Immutable mutual-fund paper orders and evidence-bound platform acknowledgements.

This module models requests and platform responses; it never claims that a paper
acknowledgement is an official AMC/RTA acceptance. A sourced response can be recorded
with its effective timestamp, while simulated responses remain explicitly labelled.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .trading_clock import IST, CalendarDomain, TradingClockError


class MFOrderError(ValueError):
    """Invalid mutual-fund paper order or acknowledgement transition."""


class MFOrderKind(StrEnum):
    PURCHASE = "PURCHASE"
    REDEMPTION = "REDEMPTION"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_OUT = "SWITCH_OUT"


class MFOrderState(StrEnum):
    REQUESTED = "REQUESTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class MFOrderEvidenceBasis(StrEnum):
    SIMULATED_ASSUMPTION = "SIMULATED_ASSUMPTION"
    OBSERVED_EVIDENCE = "OBSERVED_EVIDENCE"


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFOrderError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class MFOrder:
    """Frozen paper order, denominated by exactly one of cash or units."""

    order_id: str
    account_id: str
    folio_id: str
    scheme_code: str
    plan: str
    option: str
    transaction_type: MFOrderKind
    requested_at: datetime
    amount_inr: Decimal | None = None
    units: Decimal | None = None
    request_basis: MFOrderEvidenceBasis = MFOrderEvidenceBasis.SIMULATED_ASSUMPTION

    def __post_init__(self) -> None:
        for name in ("order_id", "account_id", "folio_id", "scheme_code", "plan", "option"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise MFOrderError(f"{name} is required (paper folio may be synthetic)")
        try:
            kind = MFOrderKind(self.transaction_type)
            basis = MFOrderEvidenceBasis(self.request_basis)
        except ValueError as exc:
            raise MFOrderError("unknown order type or evidence basis") from exc
        object.__setattr__(self, "transaction_type", kind)
        object.__setattr__(self, "request_basis", basis)
        object.__setattr__(self, "requested_at", _aware(self.requested_at, "requested_at"))
        if (self.amount_inr is None) == (self.units is None):
            raise MFOrderError("provide exactly one of amount_inr or units")
        field = "amount_inr" if self.amount_inr is not None else "units"
        try:
            value = Decimal(getattr(self, field))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MFOrderError(f"{field} must be decimal") from exc
        if not value.is_finite() or value <= 0:
            raise MFOrderError(f"{field} must be positive and finite")
        object.__setattr__(self, field, value)

    @property
    def request_sha256(self) -> str:
        payload = {
            "order_id": self.order_id, "account_id": self.account_id,
            "folio_id": self.folio_id, "scheme_code": self.scheme_code,
            "plan": self.plan, "option": self.option,
            "transaction_type": self.transaction_type.value,
            "requested_at": self.requested_at.isoformat(),
            "amount_inr": str(self.amount_inr) if self.amount_inr is not None else None,
            "units": str(self.units) if self.units is not None else None,
            "request_basis": self.request_basis.value,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MFOrderAcknowledgement:
    """A distinct platform response with effective-time and observation provenance."""

    order_id: str
    state: MFOrderState
    effective_at: datetime
    received_at: datetime
    basis: MFOrderEvidenceBasis
    source_id: str | None = None
    source_ref: str | None = None
    source_sha256: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise MFOrderError("order_id is required")
        try:
            state, basis = MFOrderState(self.state), MFOrderEvidenceBasis(self.basis)
        except ValueError as exc:
            raise MFOrderError("unknown order state or evidence basis") from exc
        if state is MFOrderState.REQUESTED:
            raise MFOrderError("acknowledgement state must be ACKNOWLEDGED, ACCEPTED or REJECTED")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "effective_at", _aware(self.effective_at, "effective_at"))
        object.__setattr__(self, "received_at", _aware(self.received_at, "received_at"))
        if self.received_at < self.effective_at:
            raise MFOrderError("response cannot be observed before its effective timestamp")
        provided = (self.source_id, self.source_ref, self.source_sha256)
        if basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
            if not all(provided):
                raise MFOrderError("observed response requires source identity, reference and hash")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_sha256 or ""):
                raise MFOrderError("source_sha256 must be a SHA-256 digest")
        elif any(provided):
            raise MFOrderError("simulated response cannot claim external source evidence")
        if state is MFOrderState.REJECTED and not (self.reason and self.reason.strip()):
            raise MFOrderError("rejection requires a reason")

    @property
    def is_official_acceptance(self) -> bool:
        """Only independently observed ACCEPTED responses assert an official point."""
        return self.state is MFOrderState.ACCEPTED and self.basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE


def platform_valid_through(order: MFOrder, *, valid_business_days: int, calendar) -> date:
    """Resolve inclusive expiry using a sourced scheme calendar; never guess holidays."""
    if type(valid_business_days) is not int or valid_business_days < 1:
        raise MFOrderError("valid_business_days must be a positive integer")
    try:
        cal = calendar.calendars[order.scheme_code]
        if cal.domain is not CalendarDomain.SCHEME:
            raise MFOrderError("order validity requires a scheme business calendar")
        target = order.requested_at.astimezone(IST).date()
        if not cal.is_open(target):
            target = cal.next_open(target)
        for _ in range(valid_business_days - 1):
            target = cal.next_open(target)
        return target
    except (KeyError, TradingClockError) as exc:
        raise MFOrderError(f"no sourced business-day validity for scheme {order.scheme_code}") from exc


__all__ = [
    "MFOrder", "MFOrderAcknowledgement", "MFOrderEvidenceBasis", "MFOrderError",
    "MFOrderKind", "MFOrderState", "platform_valid_through",
]
