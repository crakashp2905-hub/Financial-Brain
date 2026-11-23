"""Position-linked entry and exit signal types (FB-0451)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class PositionSignalError(ValueError):
    pass


class EntryAction(StrEnum):
    BUY = "BUY"
    SELL_SHORT = "SELL_SHORT"


class ExitAction(StrEnum):
    SELL_TO_CLOSE = "SELL_TO_CLOSE"
    BUY_TO_COVER = "BUY_TO_COVER"


class ExitTrigger(StrEnum):
    STOP = "STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    INVALIDATION = "INVALIDATION"
    TRAILING = "TRAILING"
    TIME = "TIME"
    MANUAL = "MANUAL"


def _uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise PositionSignalError(f"{field} must be a UUID") from exc


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PositionSignalError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EntrySignal:
    signal_id: str
    opens_position_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    action: EntryAction
    quote_source_id: str
    occurred_at: datetime
    parent_signal_id: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.signal_id, "signal_id")
        _uuid(self.opens_position_id, "opens_position_id")
        if self.parent_signal_id is not None:
            _uuid(self.parent_signal_id, "parent_signal_id")
        if not all(value.strip() for value in (self.strategy_id, self.strategy_version,
                                                self.instrument_id, self.quote_source_id)):
            raise PositionSignalError("strategy, instrument and quote source are required")
        _aware(self.occurred_at, "occurred_at")

@dataclass(frozen=True, slots=True)
class ExitSignal:
    signal_id: str
    closes_position_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    action: ExitAction
    trigger: ExitTrigger
    quote_source_id: str
    occurred_at: datetime
    parent_entry_signal_id: str

    def __post_init__(self) -> None:
        for field, value in (("signal_id", self.signal_id),
                             ("closes_position_id", self.closes_position_id),
                             ("parent_entry_signal_id", self.parent_entry_signal_id)):
            _uuid(value, field)
        if not all(value.strip() for value in (self.strategy_id, self.strategy_version,
                                                self.instrument_id, self.quote_source_id)):
            raise PositionSignalError("strategy, instrument and quote source are required")
        _aware(self.occurred_at, "occurred_at")

    def validate_against(self, entry: EntrySignal) -> None:
        expected = (ExitAction.SELL_TO_CLOSE if entry.action == EntryAction.BUY
                    else ExitAction.BUY_TO_COVER)
        if (self.closes_position_id != entry.opens_position_id
                or self.parent_entry_signal_id != entry.signal_id
                or self.instrument_id != entry.instrument_id
                or self.strategy_id != entry.strategy_id
                or self.action != expected or self.occurred_at < entry.occurred_at):
            raise PositionSignalError("exit signal does not consistently close its entry position")

    @classmethod
    def for_entry(cls, entry: EntrySignal, *, signal_id: str, trigger: ExitTrigger,
                  quote_source_id: str, occurred_at: datetime) -> "ExitSignal":
        action = (ExitAction.SELL_TO_CLOSE if entry.action == EntryAction.BUY
                  else ExitAction.BUY_TO_COVER)
        if occurred_at < entry.occurred_at:
            raise PositionSignalError("exit cannot precede entry")
        return cls(signal_id, entry.opens_position_id, entry.strategy_id, entry.strategy_version,
                   entry.instrument_id, action, trigger, quote_source_id, occurred_at,
                   entry.signal_id)
