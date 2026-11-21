"""Linked typed entry and exit signals for paper-position lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from .trader_signal import ExitTrigger


class PositionSignalError(ValueError):
    """Entry/exit signal identity or close-side semantics are invalid."""


class EntrySide(StrEnum):
    BUY = "BUY"
    SELL_SHORT = "SELL_SHORT"
    MF_SUBSCRIBE = "MF_SUBSCRIBE"


class ClosingAction(StrEnum):
    SELL_TO_CLOSE = "SELL_TO_CLOSE"
    BUY_TO_COVER = "BUY_TO_COVER"
    MF_REDEEM = "MF_REDEEM"
    MF_SWITCH = "MF_SWITCH"


def _uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise PositionSignalError(f"{name} must be a UUID") from exc


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PositionSignalError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EntrySignal:
    signal_id: str
    opens_position_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    side: EntrySide
    quote_source_id: str
    source_hash: str
    occurred_at: datetime
    parent_signal_id: str | None = None

    def __post_init__(self) -> None:
        _uuid("signal_id", self.signal_id)
        _uuid("opens_position_id", self.opens_position_id)
        if self.parent_signal_id is not None:
            _uuid("parent_signal_id", self.parent_signal_id)
        for name in ("strategy_id", "strategy_version", "instrument_id", "quote_source_id"):
            if not getattr(self, name).strip():
                raise PositionSignalError(f"{name} is required")
        if not isinstance(self.side, EntrySide):
            raise PositionSignalError("side must be an EntrySide")
        if len(self.source_hash) != 64 or any(c not in "0123456789abcdef" for c in self.source_hash):
            raise PositionSignalError("source_hash must be lowercase SHA-256")
        _aware("occurred_at", self.occurred_at)


@dataclass(frozen=True, slots=True)
class ExitSignal:
    signal_id: str
    closes_position_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    closing_action: ClosingAction
    quote_source_id: str
    source_hash: str
    occurred_at: datetime
    parent_signal_id: str
    reason: str

    def __post_init__(self) -> None:
        for name in ("signal_id", "closes_position_id", "parent_signal_id"):
            _uuid(name, getattr(self, name))
        for name in ("strategy_id", "strategy_version", "instrument_id", "quote_source_id", "reason"):
            if not getattr(self, name).strip():
                raise PositionSignalError(f"{name} is required")
        if not isinstance(self.closing_action, ClosingAction):
            raise PositionSignalError("closing_action must be a ClosingAction")
        if len(self.source_hash) != 64 or any(c not in "0123456789abcdef" for c in self.source_hash):
            raise PositionSignalError("source_hash must be lowercase SHA-256")
        _aware("occurred_at", self.occurred_at)


@dataclass(frozen=True, slots=True)
class ExitProtectionIntent:
    """Proposed position protection stored at entry; it is not an exit event."""

    protection_id: str
    position_id: str
    parent_entry_signal_id: str
    trigger: ExitTrigger
    trigger_price: Decimal
    quantity: Decimal
    created_at: datetime
    source_hash: str
    oco_group_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("protection_id", "position_id", "parent_entry_signal_id"):
            _uuid(name, getattr(self, name))
        if not isinstance(self.trigger, ExitTrigger):
            raise PositionSignalError("protection trigger must be an ExitTrigger")
        for name in ("trigger_price", "quantity"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise PositionSignalError(f"{name} must be decimal") from exc
            if not value.is_finite() or value <= 0:
                raise PositionSignalError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        _aware("created_at", self.created_at)
        if len(self.source_hash) != 64 or any(c not in "0123456789abcdef" for c in self.source_hash):
            raise PositionSignalError("source_hash must be lowercase SHA-256")
        if self.oco_group_id is not None:
            _uuid("oco_group_id", self.oco_group_id)


@dataclass(frozen=True, slots=True)
class EmittedExitEvent:
    """Timestamped exit event emitted only after a trigger has been observed."""

    exit_signal: ExitSignal
    protection_id: str
    trigger: ExitTrigger
    idempotency_key: str
    quantity: Decimal
    observed_trigger_at: datetime
    oco_group_id: str | None = None

    def __post_init__(self) -> None:
        _uuid("protection_id", self.protection_id)
        if not isinstance(self.trigger, ExitTrigger):
            raise PositionSignalError("emitted exit trigger must be an ExitTrigger")
        if not self.idempotency_key.strip():
            raise PositionSignalError("exit idempotency_key is required")
        if self.oco_group_id is not None:
            _uuid("oco_group_id", self.oco_group_id)
        try:
            quantity = Decimal(self.quantity)
        except (InvalidOperation, TypeError) as exc:
            raise PositionSignalError("exit quantity must be decimal") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise PositionSignalError("exit quantity must be finite and positive")
        object.__setattr__(self, "quantity", quantity)
        _aware("observed_trigger_at", self.observed_trigger_at)
        if self.exit_signal.occurred_at < self.observed_trigger_at:
            raise PositionSignalError("emitted exit cannot precede its observed trigger")


def validate_protection_emission(
    entry: EntrySignal,
    protection: ExitProtectionIntent,
    event: EmittedExitEvent,
    *,
    open_quantity: Decimal,
) -> None:
    """Bind the observed trigger event to its entry/protection without over-closing."""
    if protection.position_id != entry.opens_position_id:
        raise PositionSignalError("protection belongs to a different position")
    if protection.parent_entry_signal_id != entry.signal_id:
        raise PositionSignalError("protection does not identify its entry signal")
    if protection.trigger is not event.trigger or protection.protection_id != event.protection_id:
        raise PositionSignalError("emitted exit differs from its protection intent")
    if protection.oco_group_id != event.oco_group_id:
        raise PositionSignalError("emitted exit differs from its protection OCO group")
    if event.exit_signal.parent_signal_id != entry.signal_id:
        raise PositionSignalError("emitted exit does not identify its entry signal")
    validate_entry_exit_link(entry, event.exit_signal)
    try:
        remaining = Decimal(open_quantity)
    except (InvalidOperation, TypeError) as exc:
        raise PositionSignalError("open_quantity must be decimal") from exc
    if not remaining.is_finite() or remaining <= 0 or event.quantity > remaining:
        raise PositionSignalError("emitted exit quantity exceeds open position quantity")
    if event.quantity > protection.quantity:
        raise PositionSignalError("emitted exit quantity exceeds protected quantity")
    if event.observed_trigger_at < protection.created_at:
        raise PositionSignalError("trigger observation precedes protection intent")


def validate_entry_exit_link(entry: EntrySignal, exit_signal: ExitSignal) -> None:
    """Reject exits linked to a different position, strategy, or direction."""
    if exit_signal.closes_position_id != entry.opens_position_id:
        raise PositionSignalError("exit closes a different position")
    if exit_signal.parent_signal_id != entry.signal_id:
        raise PositionSignalError("exit parent does not identify its entry signal")
    if (exit_signal.strategy_id, exit_signal.strategy_version, exit_signal.instrument_id) != (
        entry.strategy_id, entry.strategy_version, entry.instrument_id
    ):
        raise PositionSignalError("exit strategy or instrument differs from its entry")
    expected = {
        EntrySide.BUY: ClosingAction.SELL_TO_CLOSE,
        EntrySide.SELL_SHORT: ClosingAction.BUY_TO_COVER,
        EntrySide.MF_SUBSCRIBE: ClosingAction.MF_REDEEM,
    }[entry.side]
    if exit_signal.closing_action not in (
        {ClosingAction.MF_REDEEM, ClosingAction.MF_SWITCH}
        if entry.side is EntrySide.MF_SUBSCRIBE else {expected}
    ):
        raise PositionSignalError("closing action does not reverse the entry side")
    if exit_signal.occurred_at < entry.occurred_at:
        raise PositionSignalError("exit cannot precede entry")


__all__ = ["ClosingAction", "EmittedExitEvent", "EntrySide", "EntrySignal", "ExitProtectionIntent",
           "ExitSignal", "PositionSignalError", "validate_entry_exit_link", "validate_protection_emission"]
