"""Paired asynchronous mutual-fund switch lifecycle for paper trading."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import cast

from .units import INRNotional, MutualFundUnits, NavPerUnit, UnitError


class MFSwitchError(ValueError):
    """Invalid or out-of-order MF switch lifecycle event."""


class MFSwitchEventKind(StrEnum):
    SWITCH_OUT_VALUE_CONFIRMED = "SWITCH_OUT_VALUE_CONFIRMED"
    SWITCH_OUT_PROCEEDS_AVAILABLE = "SWITCH_OUT_PROCEEDS_AVAILABLE"
    SWITCH_IN_FUNDS_APPLIED = "SWITCH_IN_FUNDS_APPLIED"
    SWITCH_IN_ALLOTMENT_CONFIRMED = "SWITCH_IN_ALLOTMENT_CONFIRMED"


class MFSwitchBasis(StrEnum):
    SIMULATED_ASSUMPTION = "SIMULATED_ASSUMPTION"
    OBSERVED_EVIDENCE = "OBSERVED_EVIDENCE"


_EVENT_ORDER = tuple(MFSwitchEventKind)
_UNIT_QUANTUM = Decimal("0.000000000000000001")


def _decimal(value: Decimal | str | int, label: str) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MFSwitchError(f"{label} must be decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise MFSwitchError(f"{label} must be positive and finite")
    return amount


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFSwitchError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class MFSwitchRequest:
    switch_id: str
    account_id: str
    folio_id: str
    source_scheme: str
    source_plan: str
    source_option: str
    source_isin: str
    target_scheme: str
    target_plan: str
    target_option: str
    target_isin: str
    requested_units: Decimal | MutualFundUnits
    requested_at: datetime

    def __post_init__(self) -> None:
        for name in ("switch_id", "account_id", "folio_id", "source_scheme", "source_plan",
                     "source_option", "source_isin", "target_scheme", "target_plan",
                     "target_option", "target_isin"):
            if not getattr(self, name).strip():
                raise MFSwitchError(f"{name} is required")
        if (self.source_scheme, self.source_plan, self.source_option) == (
                self.target_scheme, self.target_plan, self.target_option):
            raise MFSwitchError("source and target scheme identities must differ")
        try:
            raw_units = (self.requested_units.units
                         if isinstance(self.requested_units, MutualFundUnits)
                         else self.requested_units)
        except UnitError as exc:
            raise MFSwitchError(str(exc)) from exc
        units = _decimal(raw_units, "requested_units")
        if units.quantize(_UNIT_QUANTUM) != units:
            raise MFSwitchError("requested_units exceed paper unit precision")
        object.__setattr__(self, "requested_units", units)
        object.__setattr__(self, "requested_at", _aware(self.requested_at, "requested_at"))


@dataclass(frozen=True, slots=True)
class MFSwitchEvent:
    event_id: str
    kind: MFSwitchEventKind
    effective_at: datetime
    basis: MFSwitchBasis
    amount_inr: Decimal | INRNotional | None = None
    units: Decimal | MutualFundUnits | None = None
    nav_per_unit: Decimal | NavPerUnit | None = None
    source_id: str | None = None
    source_ref: str | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise MFSwitchError("event_id is required")
        try:
            object.__setattr__(self, "kind", MFSwitchEventKind(self.kind))
            basis = MFSwitchBasis(self.basis)
        except ValueError as exc:
            raise MFSwitchError("unknown switch event or evidence basis") from exc
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "effective_at", _aware(self.effective_at, "effective_at"))
        try:
            for name, unit_type, value_attr in (
                ("amount_inr", INRNotional, "amount"),
                ("units", MutualFundUnits, "units"),
                ("nav_per_unit", NavPerUnit, "amount"),
            ):
                value = getattr(self, name)
                if value is not None:
                    normalized = value if isinstance(value, unit_type) else unit_type(value)
                    object.__setattr__(self, name, _decimal(getattr(normalized, value_attr), name))
        except UnitError as exc:
            raise MFSwitchError(str(exc)) from exc
        if self.kind in (MFSwitchEventKind.SWITCH_OUT_VALUE_CONFIRMED,
                         MFSwitchEventKind.SWITCH_OUT_PROCEEDS_AVAILABLE,
                         MFSwitchEventKind.SWITCH_IN_FUNDS_APPLIED):
            if self.amount_inr is None or self.units is not None or self.nav_per_unit is not None:
                raise MFSwitchError("switch value/funding events require only amount_inr")
        elif self.amount_inr is None or self.units is None or self.nav_per_unit is None:
            raise MFSwitchError("allotment event requires amount, units and NAV")
        if (self.kind is MFSwitchEventKind.SWITCH_IN_ALLOTMENT_CONFIRMED
                and self.units is not None and self.nav_per_unit is not None):
            if self.amount_inr is None:
                raise MFSwitchError("allotment event requires amount, units and NAV")
            units = cast(Decimal, self.units)
            nav = cast(Decimal, self.nav_per_unit)
            amount = cast(Decimal, self.amount_inr)
            if (units * nav).quantize(Decimal("0.01")) != amount.quantize(Decimal("0.01")):
                raise MFSwitchError("allotment units x NAV must reconcile to applied amount")
        provenance = (self.source_id, self.source_ref, self.source_sha256)
        if basis is MFSwitchBasis.OBSERVED_EVIDENCE:
            if not all(provenance):
                raise MFSwitchError("observed switch event requires source provenance")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_sha256 or ""):
                raise MFSwitchError("source_sha256 must be a SHA-256 digest")
        elif any(provenance):
            raise MFSwitchError("simulated switch event cannot claim external evidence")

    @property
    def amount(self) -> Decimal:
        if self.amount_inr is None:
            raise MFSwitchError("event has no monetary amount")
        return cast(Decimal, self.amount_inr)


@dataclass(frozen=True, slots=True)
class MFSwitchLifecycle:
    request: MFSwitchRequest
    events: tuple[MFSwitchEvent, ...] = ()

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if any(not isinstance(item, MFSwitchEvent) for item in events):
            raise MFSwitchError("events must be typed MFSwitchEvent values")
        if len({item.event_id for item in events}) != len(events):
            raise MFSwitchError("event IDs must be unique")
        if len(events) > len(_EVENT_ORDER):
            raise MFSwitchError("too many switch lifecycle events")
        for index, event in enumerate(events):
            if event.kind is not _EVENT_ORDER[index]:
                raise MFSwitchError("switch lifecycle events must follow out-value, proceeds, funding, allotment")
            if event.effective_at < self.request.requested_at:
                raise MFSwitchError("switch event precedes request")
            if index and event.effective_at <= events[index - 1].effective_at:
                raise MFSwitchError("switch stages must occur asynchronously in strict timestamp order")
        if len(events) >= 2 and events[0].amount != events[1].amount:
            raise MFSwitchError("available switch proceeds must match confirmed net value")
        if len(events) >= 3 and events[2].amount > events[1].amount:
            raise MFSwitchError("switch-in funding exceeds available switch-out proceeds")
        if len(events) >= 4 and events[3].amount != events[2].amount:
            raise MFSwitchError("switch allotment amount must match applied funds")
        object.__setattr__(self, "events", events)

    def append(self, event: MFSwitchEvent) -> MFSwitchLifecycle:
        """Return the next lifecycle version; no balance is spendable before proceeds settle."""
        return MFSwitchLifecycle(self.request, self.events + (event,))

    @property
    def stage(self) -> str:
        return "REQUESTED" if not self.events else self.events[-1].kind.value

    @property
    def proceeds_available_inr(self) -> Decimal:
        if len(self.events) < 2:
            return Decimal(0)
        return self.events[1].amount

    @property
    def unapplied_proceeds_inr(self) -> Decimal:
        if len(self.events) < 2:
            return Decimal(0)
        funded = self.events[2].amount if len(self.events) >= 3 else Decimal(0)
        return self.proceeds_available_inr - funded

    @property
    def state_sha256(self) -> str:
        payload = {
            "switch_id": self.request.switch_id,
            "events": [{"event_id": item.event_id, "kind": item.kind.value,
                        "effective_at": item.effective_at.isoformat(),
                        "amount_inr": str(item.amount_inr) if item.amount_inr is not None else None,
                        "units": str(item.units) if item.units is not None else None,
                        "nav_per_unit": str(item.nav_per_unit) if item.nav_per_unit is not None else None,
                        "basis": item.basis.value, "source_id": item.source_id,
                        "source_ref": item.source_ref, "source_sha256": item.source_sha256}
                       for item in self.events],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                              .encode("utf-8")).hexdigest()


__all__ = [
    "MFSwitchBasis", "MFSwitchError", "MFSwitchEvent", "MFSwitchEventKind",
    "MFSwitchLifecycle", "MFSwitchRequest",
]
