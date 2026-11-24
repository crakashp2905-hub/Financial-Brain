"""Independent mutual-fund SIP installment outcomes and allotment NAV records."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .units import INRNotional, MutualFundUnits, NavPerUnit, UnitError



class MFSIPError(ValueError):
    """Invalid SIP installment evidence or lifecycle transition."""


class MFSIPState(StrEnum):
    SCHEDULED = "SCHEDULED"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    FUNDS_AVAILABLE = "FUNDS_AVAILABLE"
    NAV_PENDING = "NAV_PENDING"
    MISSED = "MISSED"
    ALLOTTED = "ALLOTTED"


def _aware(value: datetime | None, label: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFSIPError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _positive(value: Decimal | str | int | None, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MFSIPError(f"{label} must be decimal") from exc
    if not result.is_finite() or result <= 0:
        raise MFSIPError(f"{label} must be positive and finite")
    return result


@dataclass(frozen=True, slots=True)
class MFSIPInstallment:
    installment_id: str
    plan_id: str
    account_id: str
    folio_id: str
    scheme_code: str
    plan: str
    option: str
    scheduled_for: date
    amount_inr: Decimal | INRNotional
    state: MFSIPState = MFSIPState.SCHEDULED
    payment_initiated_at: datetime | None = None
    funds_available_at: datetime | None = None
    missed_at: datetime | None = None
    miss_reason: str | None = None
    nav_date: date | None = None
    nav_per_unit: Decimal | NavPerUnit | None = None
    nav_published_at: datetime | None = None
    nav_available_at: datetime | None = None
    allotted_at: datetime | None = None
    units_allotted: Decimal | MutualFundUnits | None = None

    def __post_init__(self) -> None:
        for name in ("installment_id", "plan_id", "account_id", "folio_id", "scheme_code",
                     "plan", "option"):
            if not getattr(self, name).strip():
                raise MFSIPError(f"{name} is required")
        try:
            state = MFSIPState(self.state)
        except ValueError as exc:
            raise MFSIPError("unknown SIP installment state") from exc
        object.__setattr__(self, "state", state)
        try:
            amount_unit = (self.amount_inr if isinstance(self.amount_inr, INRNotional)
                           else INRNotional(self.amount_inr))
            amount = _positive(amount_unit.amount, "amount_inr")
        except UnitError as exc:
            raise MFSIPError(str(exc)) from exc
        if amount is None:
            raise MFSIPError("amount_inr is required")
        object.__setattr__(self, "amount_inr", amount)
        for name in ("payment_initiated_at", "funds_available_at", "missed_at",
                     "nav_published_at", "nav_available_at", "allotted_at"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        try:
            nav_unit = (self.nav_per_unit if isinstance(self.nav_per_unit, NavPerUnit)
                        else NavPerUnit(self.nav_per_unit)) if self.nav_per_unit is not None else None
            units_unit = (self.units_allotted if isinstance(self.units_allotted, MutualFundUnits)
                          else MutualFundUnits(self.units_allotted)) if self.units_allotted is not None else None
        except UnitError as exc:
            raise MFSIPError(str(exc)) from exc
        nav = _positive(nav_unit.amount if nav_unit is not None else None, "nav_per_unit")
        units = _positive(units_unit.units if units_unit is not None else None, "units_allotted")
        object.__setattr__(self, "nav_per_unit", nav)
        object.__setattr__(self, "units_allotted", units)
        if state is MFSIPState.SCHEDULED:
            if any((self.payment_initiated_at, self.funds_available_at, self.missed_at,
                    self.nav_date, nav, self.nav_published_at, self.nav_available_at,
                    self.allotted_at, units, self.miss_reason)):
                raise MFSIPError("scheduled installment cannot claim payment, miss or allotment evidence")
        elif state is MFSIPState.PAYMENT_INITIATED:
            if self.payment_initiated_at is None or any((self.funds_available_at, self.missed_at,
                    self.nav_date, nav, self.nav_published_at, self.nav_available_at,
                    self.allotted_at, units, self.miss_reason)):
                raise MFSIPError("payment-initiated state requires only its initiation timestamp")
        elif state is MFSIPState.FUNDS_AVAILABLE:
            if self.payment_initiated_at is None or self.funds_available_at is None:
                raise MFSIPError("funds-available state requires both payment timestamps")
            if self.payment_initiated_at > self.funds_available_at:
                raise MFSIPError("funds cannot be available before payment initiation")
            if any((self.missed_at, self.nav_date, nav, self.nav_published_at,
                    self.nav_available_at, self.allotted_at, units, self.miss_reason)):
                raise MFSIPError("funds-available state cannot claim NAV allotment evidence")
        elif state is MFSIPState.NAV_PENDING:
            if self.payment_initiated_at is None or self.funds_available_at is None:
                raise MFSIPError("NAV_PENDING requires independently known payment milestones")
            if self.payment_initiated_at > self.funds_available_at:
                raise MFSIPError("funds cannot be available before payment initiation")
            if any((self.missed_at, nav, self.nav_published_at,
                    self.nav_available_at, self.allotted_at, units, self.miss_reason)):
                raise MFSIPError("NAV_PENDING must not fabricate NAV or units")
        elif state is MFSIPState.MISSED:
            if self.missed_at is None or not (self.miss_reason and self.miss_reason.strip()):
                raise MFSIPError("missed installment requires timestamp and reason")
            if any((self.funds_available_at, self.nav_date, nav, self.nav_published_at,
                    self.nav_available_at, self.allotted_at, units)):
                raise MFSIPError("missed installment cannot also claim funds or allotment")
            if self.payment_initiated_at and self.missed_at < self.payment_initiated_at:
                raise MFSIPError("miss timestamp precedes payment initiation")
        elif state is MFSIPState.ALLOTTED:
            if self.missed_at is not None or self.miss_reason is not None:
                raise MFSIPError("allotted installment cannot also be marked missed")
            if any(value is None for value in (self.payment_initiated_at, self.funds_available_at,
                    self.nav_date, nav, self.nav_available_at,
                    self.allotted_at, units)):
                raise MFSIPError("allotted installment requires payment, NAV availability and unit evidence")
            assert self.payment_initiated_at is not None and self.funds_available_at is not None
            assert self.nav_date is not None and nav is not None and units is not None
            assert self.nav_available_at is not None
            assert self.allotted_at is not None
            if self.payment_initiated_at > self.funds_available_at:
                raise MFSIPError("funds cannot be available before payment initiation")
            if ((self.nav_published_at is not None and self.nav_published_at > self.nav_available_at)
                    or self.nav_available_at > self.allotted_at):
                raise MFSIPError("NAV publication, availability and allotment clocks are out of order")
            if (units * nav).quantize(Decimal("0.01")) != amount.quantize(Decimal("0.01")):
                raise MFSIPError("allotted units x NAV must reconcile to installment amount")


_ALLOWED = {
    MFSIPState.SCHEDULED: {MFSIPState.PAYMENT_INITIATED, MFSIPState.MISSED},
    MFSIPState.PAYMENT_INITIATED: {MFSIPState.FUNDS_AVAILABLE, MFSIPState.MISSED},
    MFSIPState.FUNDS_AVAILABLE: {MFSIPState.NAV_PENDING, MFSIPState.ALLOTTED},
    MFSIPState.NAV_PENDING: {MFSIPState.ALLOTTED},
    MFSIPState.MISSED: set(),
    MFSIPState.ALLOTTED: set(),
}


def validate_installment_transition(previous: MFSIPInstallment,
                                    following: MFSIPInstallment) -> None:
    """Enforce legal per-installment transitions; a missed payment stays missed."""
    stable = ("installment_id", "plan_id", "account_id", "folio_id", "scheme_code",
              "plan", "option", "scheduled_for", "amount_inr")
    if any(getattr(previous, name) != getattr(following, name) for name in stable):
        raise MFSIPError("installment identity and schedule are immutable")
    if following.state not in _ALLOWED[previous.state]:
        raise MFSIPError(f"illegal SIP transition {previous.state.value} -> {following.state.value}")


@dataclass(frozen=True, slots=True)
class MFSIPSeries:
    """Schedule of independent installment records; each payment/NAV stands alone."""

    plan_id: str
    installments: tuple[MFSIPInstallment, ...]

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise MFSIPError("plan_id is required")
        installments = tuple(self.installments)
        if any(item.plan_id != self.plan_id for item in installments):
            raise MFSIPError("all installments must belong to this SIP plan")
        if len({item.installment_id for item in installments}) != len(installments):
            raise MFSIPError("installment IDs must be unique")
        if len({item.scheduled_for for item in installments}) != len(installments):
            raise MFSIPError("a SIP plan cannot contain duplicate scheduled dates")
        object.__setattr__(self, "installments", installments)

    def advance(self, following: MFSIPInstallment) -> MFSIPSeries:
        """Replace one snapshot only through the explicit per-installment state machine."""
        matches = [index for index, item in enumerate(self.installments)
                   if item.installment_id == following.installment_id]
        if len(matches) != 1:
            raise MFSIPError("installment does not belong to this SIP series")
        index = matches[0]
        validate_installment_transition(self.installments[index], following)
        updated = self.installments[:index] + (following,) + self.installments[index + 1:]
        return MFSIPSeries(self.plan_id, updated)


__all__ = ["MFSIPError", "MFSIPInstallment", "MFSIPSeries", "MFSIPState",
           "validate_installment_transition"]
