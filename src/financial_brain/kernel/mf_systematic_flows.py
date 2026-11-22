"""Recurring SWP/STP instructions with asynchronous redemption settlement."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from .trading_clock import IST


class MFSystematicFlowError(ValueError):
    """Invalid or out-of-order SWP/STP instruction or event."""


class MFSystematicFlowKind(StrEnum):
    SWP = "SWP"
    STP = "STP"


class MFSystematicFlowEventKind(StrEnum):
    REDEMPTION_REQUESTED = "REDEMPTION_REQUESTED"
    VALUE_CONFIRMED = "VALUE_CONFIRMED"
    PAYOUT_AVAILABLE = "PAYOUT_AVAILABLE"
    PURCHASE_FUNDS_APPLIED = "PURCHASE_FUNDS_APPLIED"
    ALLOTMENT_CONFIRMED = "ALLOTMENT_CONFIRMED"
    PURCHASE_CANCELLED = "PURCHASE_CANCELLED"
    MISSED = "MISSED"


class MFSystematicFlowBasis(StrEnum):
    SIMULATED_ASSUMPTION = "SIMULATED_ASSUMPTION"
    OBSERVED_EVIDENCE = "OBSERVED_EVIDENCE"


def _positive(value: Decimal | str | int | None, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MFSystematicFlowError(f"{label} must be decimal") from exc
    if not result.is_finite() or result <= 0:
        raise MFSystematicFlowError(f"{label} must be positive and finite")
    return result


def _nonnegative(value: Decimal | str | int | None, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MFSystematicFlowError(f"{label} must be decimal") from exc
    if not result.is_finite() or result < 0:
        raise MFSystematicFlowError(f"{label} must be finite and non-negative")
    return result


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFSystematicFlowError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class MFSystematicFlowInstruction:
    instruction_id: str
    plan_id: str
    account_id: str
    folio_id: str
    kind: MFSystematicFlowKind
    scheduled_for: date
    source_scheme: str
    source_plan: str
    source_option: str
    source_isin: str
    amount_inr: Decimal | None = None
    units: Decimal | None = None
    target_scheme: str | None = None
    target_plan: str | None = None
    target_option: str | None = None
    target_isin: str | None = None

    def __post_init__(self) -> None:
        for name in ("instruction_id", "plan_id", "account_id", "folio_id", "source_scheme",
                     "source_plan", "source_option", "source_isin"):
            if not getattr(self, name).strip():
                raise MFSystematicFlowError(f"{name} is required")
        try:
            kind = MFSystematicFlowKind(self.kind)
        except ValueError as exc:
            raise MFSystematicFlowError("unknown systematic flow kind") from exc
        object.__setattr__(self, "kind", kind)
        if (self.amount_inr is None) == (self.units is None):
            raise MFSystematicFlowError("specify exactly one withdrawal amount or units")
        object.__setattr__(self, "amount_inr", _positive(self.amount_inr, "amount_inr"))
        object.__setattr__(self, "units", _positive(self.units, "units"))
        targets = (self.target_scheme, self.target_plan, self.target_option, self.target_isin)
        if kind is MFSystematicFlowKind.STP:
            if not all(target and target.strip() for target in targets):
                raise MFSystematicFlowError("STP instruction requires complete target scheme identity")
            if (self.source_scheme, self.source_plan, self.source_option) == (
                    self.target_scheme, self.target_plan, self.target_option):
                raise MFSystematicFlowError("STP source and target scheme identities must differ")
        elif any(target is not None for target in targets):
            raise MFSystematicFlowError("SWP instruction cannot carry a target purchase scheme")


@dataclass(frozen=True, slots=True)
class MFSystematicFlowEvent:
    event_id: str
    kind: MFSystematicFlowEventKind
    effective_at: datetime
    basis: MFSystematicFlowBasis
    amount_inr: Decimal | None = None
    gross_inr: Decimal | None = None
    exit_load_inr: Decimal | None = None
    other_charges_inr: Decimal | None = None
    nav_per_unit: Decimal | None = None
    units: Decimal | None = None
    reason: str | None = None
    source_id: str | None = None
    source_ref: str | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise MFSystematicFlowError("event_id is required")
        try:
            object.__setattr__(self, "kind", MFSystematicFlowEventKind(self.kind))
            basis = MFSystematicFlowBasis(self.basis)
        except ValueError as exc:
            raise MFSystematicFlowError("unknown event or evidence basis") from exc
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "effective_at", _aware(self.effective_at, "effective_at"))
        for name in ("amount_inr", "gross_inr", "nav_per_unit", "units"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        for name in ("exit_load_inr", "other_charges_inr"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonnegative(value, name))
        if self.kind is MFSystematicFlowEventKind.VALUE_CONFIRMED:
            if self.amount_inr is None or self.gross_inr is None:
                raise MFSystematicFlowError("confirmed redemption requires net and gross value")
            if self.exit_load_inr is None or self.other_charges_inr is None:
                raise MFSystematicFlowError("confirmed redemption requires explicit load and other-charge amounts")
            charges = self.exit_load_inr + self.other_charges_inr
            if (self.gross_inr - charges).quantize(Decimal("0.01")) != self.amount_inr.quantize(Decimal("0.01")):
                raise MFSystematicFlowError("net redemption value must reconcile to gross less charges")
            if (self.units is None) != (self.nav_per_unit is None):
                raise MFSystematicFlowError("redemption unit and NAV evidence must be recorded together")
            if self.units is not None and self.nav_per_unit is not None:
                assert self.gross_inr is not None
                if (self.units * self.nav_per_unit).quantize(Decimal("0.01")) != self.gross_inr.quantize(Decimal("0.01")):
                    raise MFSystematicFlowError("redeemed units x NAV must reconcile to gross value")
        elif self.kind in (MFSystematicFlowEventKind.PAYOUT_AVAILABLE,
                           MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED,
                           MFSystematicFlowEventKind.PURCHASE_CANCELLED):
            if self.amount_inr is None:
                raise MFSystematicFlowError("payout/funding event requires amount")
        elif self.kind is MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED:
            if any(value is None for value in (self.amount_inr, self.nav_per_unit, self.units)):
                raise MFSystematicFlowError("allotment requires amount, NAV and units")
            assert self.amount_inr is not None and self.nav_per_unit is not None and self.units is not None
            if (self.nav_per_unit * self.units).quantize(Decimal("0.01")) != self.amount_inr.quantize(Decimal("0.01")):
                raise MFSystematicFlowError("allotted units x NAV must reconcile to applied amount")
        elif self.kind is MFSystematicFlowEventKind.MISSED:
            if not (self.reason and self.reason.strip()):
                raise MFSystematicFlowError("missed event requires a reason")
        if (self.kind is MFSystematicFlowEventKind.PURCHASE_CANCELLED
                and not (self.reason and self.reason.strip())):
            raise MFSystematicFlowError("cancelled STP purchase requires a reason")
        provenance = (self.source_id, self.source_ref, self.source_sha256)
        if basis is MFSystematicFlowBasis.OBSERVED_EVIDENCE:
            if not all(provenance) or not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_sha256 or ""):
                raise MFSystematicFlowError("observed event requires source identity, reference and SHA-256")
        elif any(provenance):
            raise MFSystematicFlowError("simulated event cannot claim external source evidence")

    @property
    def amount(self) -> Decimal:
        if self.amount_inr is None:
            raise MFSystematicFlowError("event has no monetary amount")
        return self.amount_inr


@dataclass(frozen=True, slots=True)
class MFSystematicFlowLifecycle:
    instruction: MFSystematicFlowInstruction
    events: tuple[MFSystematicFlowEvent, ...] = ()

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if any(not isinstance(event, MFSystematicFlowEvent) for event in events):
            raise MFSystematicFlowError("events must be typed systematic flow records")
        if len({event.event_id for event in events}) != len(events):
            raise MFSystematicFlowError("systematic event IDs must be unique")
        kinds = [event.kind for event in events]
        expected = [MFSystematicFlowEventKind.REDEMPTION_REQUESTED,
                    MFSystematicFlowEventKind.VALUE_CONFIRMED,
                    MFSystematicFlowEventKind.PAYOUT_AVAILABLE]
        if self.instruction.kind is MFSystematicFlowKind.STP:
            expected.append(MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED)
            if kinds[-1:] == [MFSystematicFlowEventKind.PURCHASE_CANCELLED]:
                expected.append(MFSystematicFlowEventKind.PURCHASE_CANCELLED)
            elif len(kinds) >= 5:
                expected.append(MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED)
        if kinds == [MFSystematicFlowEventKind.MISSED]:
            if events[0].effective_at.astimezone(IST).date() < self.instruction.scheduled_for:
                raise MFSystematicFlowError("missed event precedes its scheduled date")
            object.__setattr__(self, "events", events)
            return
        if kinds != expected[:len(kinds)]:
            raise MFSystematicFlowError("events violate SWP/STP redemption-to-payout ordering")
        if any(event.effective_at.astimezone(IST).date() < self.instruction.scheduled_for
               for event in events):
            raise MFSystematicFlowError("SWP/STP event precedes its scheduled date")
        for prior, following in zip(events, events[1:]):
            if following.effective_at <= prior.effective_at:
                raise MFSystematicFlowError("SWP/STP settlement stages must be asynchronous")
        if len(events) >= 2:
            confirmed = events[1]
            if confirmed.kind is not MFSystematicFlowEventKind.VALUE_CONFIRMED:
                raise MFSystematicFlowError("redemption value confirmation is missing")
        if len(events) >= 3 and events[2].amount != events[1].amount:
            raise MFSystematicFlowError("payout must equal confirmed net redemption value")
        if len(events) >= 4 and events[3].amount > events[2].amount:
            raise MFSystematicFlowError("STP purchase cannot use more than settled proceeds")
        if len(events) >= 5 and events[4].amount != events[3].amount:
            raise MFSystematicFlowError("STP allotment amount must equal applied purchase funds")
        if (len(events) >= 5 and events[4].kind is MFSystematicFlowEventKind.PURCHASE_CANCELLED
                and events[4].amount != events[3].amount):
            raise MFSystematicFlowError("STP cancellation must release the full reserved purchase")
        object.__setattr__(self, "events", events)

    def append(self, event: MFSystematicFlowEvent) -> MFSystematicFlowLifecycle:
        return MFSystematicFlowLifecycle(self.instruction, self.events + (event,))

    @property
    def stage(self) -> str:
        return "SCHEDULED" if not self.events else self.events[-1].kind.value

    @property
    def available_to_redeploy_inr(self) -> Decimal:
        if len(self.events) < 3 or self.events[2].kind is not MFSystematicFlowEventKind.PAYOUT_AVAILABLE:
            return Decimal(0)
        if len(self.events) < 4:
            return self.events[2].amount
        if (len(self.events) >= 5
                and self.events[4].kind is MFSystematicFlowEventKind.PURCHASE_CANCELLED):
            return self.events[2].amount
        return self.events[2].amount - self.events[3].amount

    @property
    def state_sha256(self) -> str:
        payload = {"instruction_id": self.instruction.instruction_id,
                   "events": [{"event_id": event.event_id, "kind": event.kind.value,
                               "effective_at": event.effective_at.isoformat(),
                               "amount_inr": str(event.amount_inr) if event.amount_inr is not None else None,
                               "basis": event.basis.value,
                               "source_id": event.source_id, "source_ref": event.source_ref,
                               "source_sha256": event.source_sha256}
                              for event in self.events]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()


__all__ = [
    "MFSystematicFlowBasis", "MFSystematicFlowError", "MFSystematicFlowEvent",
    "MFSystematicFlowEventKind", "MFSystematicFlowInstruction",
    "MFSystematicFlowKind", "MFSystematicFlowLifecycle",
]
