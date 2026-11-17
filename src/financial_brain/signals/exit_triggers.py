"""Paper exit triggers for a protected position (checklist 4A: FB-0463 to FB-0472).

A planned exit (stop, target) is separate from an emitted exit event and from a simulated fill.
This module turns a position's precommitted plan plus observed bars into timestamped events:

* the first of stop or target to trigger closes the position and cancels its sibling (paper OCO);
* if one bar touches both and the order inside it is unknown, the stop is assumed first (adverse);
* a bar that opens through the stop or target fills at the open, not at the trigger price, so a gap
  loss beyond the planned stop is kept;
* a bar with no usable price leaves an UNRESOLVED exit intent instead of a fictional fill;
* exits never exceed the open quantity, and replaying the same bar emits nothing new.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ExitError(ValueError):
    pass


class Event(str, Enum):
    STOP_TP_ARMED = "STOP_TP_ARMED"
    EXIT_TRIGGER = "EXIT_TRIGGER"
    EXIT_FILL = "EXIT_FILL"
    EXIT_UNRESOLVED = "EXIT_UNRESOLVED"
    POSITION_CLOSED = "POSITION_CLOSED"


@dataclass(frozen=True)
class Bar:
    at: datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None

    @property
    def priced(self) -> bool:
        return None not in (self.open, self.high, self.low, self.close)


@dataclass(frozen=True)
class Record:
    kind: Event
    at: datetime
    key: str
    reason: str = ""
    price: Decimal | None = None
    quantity: Decimal | None = None


@dataclass
class ProtectedPosition:
    position_id: str
    long: bool
    quantity: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal
    ledger: list[Record] = field(default_factory=list)
    open_quantity: Decimal = Decimal(0)
    pending: str | None = None   # trigger awaiting a price

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ExitError("quantity must be positive")
        ok = self.stop < self.entry < self.target if self.long else self.target < self.entry < self.stop
        if not ok:
            raise ExitError("protection is on the wrong side of entry")
        self.open_quantity = self.quantity

    @property
    def closed(self) -> bool:
        return self.open_quantity == 0

    def _emit(self, rec: Record) -> None:
        if any(r.key == rec.key for r in self.ledger):
            return
        self.ledger.append(rec)

    def arm(self, at: datetime) -> None:
        self._emit(Record(Event.STOP_TP_ARMED, at, f"{self.position_id}:armed"))

    def on_bar(self, bar: Bar) -> None:
        if self.closed:
            return
        key = f"{self.position_id}:{bar.at.isoformat()}"
        if any(r.key.startswith(key) for r in self.ledger):
            return  # idempotent replay
        if not bar.priced:
            if self.pending:
                self._emit(Record(Event.EXIT_UNRESOLVED, bar.at, key + ":unresolved",
                                  f"{self.pending} without a price"))
            return
        o, h, lo = bar.open, bar.high, bar.low
        assert o is not None and h is not None and lo is not None
        if self.pending:
            return self._fill(bar.at, key, self.pending, o)
        if self.long:
            hit_stop, hit_tp = lo <= self.stop, h >= self.target
            gap_stop, gap_tp = o <= self.stop, o >= self.target
        else:
            hit_stop, hit_tp = h >= self.stop, lo <= self.target
            gap_stop, gap_tp = o >= self.stop, o <= self.target
        if gap_stop:
            return self._fill(bar.at, key, "HARD_STOP", o)
        if gap_tp:
            return self._fill(bar.at, key, "TAKE_PROFIT", o)
        if hit_stop:   # adverse-first when both are touched in one bar
            return self._fill(bar.at, key, "HARD_STOP", self.stop)
        if hit_tp:
            return self._fill(bar.at, key, "TAKE_PROFIT", self.target)

    def partial_exit(self, at: datetime, quantity: Decimal, price: Decimal, reason: str) -> Decimal:
        """A scale-out or partial fill: SL/TP stay armed for the remainder; never oversells (FB-0464)."""
        if quantity <= 0:
            raise ExitError("quantity must be positive")
        qty = min(quantity, self.open_quantity)
        key = f"{self.position_id}:{at.isoformat()}:partial:{reason}"
        if any(r.key == key + ":fill" for r in self.ledger):
            return self.open_quantity
        self._emit(Record(Event.EXIT_FILL, at, key + ":fill", reason, price, qty))
        self.open_quantity -= qty
        if self.open_quantity == 0:
            self._emit(Record(Event.POSITION_CLOSED, at, key + ":closed", reason))
        return self.open_quantity

    def trigger_without_price(self, at: datetime, reason: str) -> None:
        """A trigger seen during a halt or outage: persist intent, fill nothing."""
        self.pending = reason
        self._emit(Record(Event.EXIT_TRIGGER, at, f"{self.position_id}:{at.isoformat()}:trig", reason))
        self._emit(Record(Event.EXIT_UNRESOLVED, at, f"{self.position_id}:{at.isoformat()}:unres",
                          reason))

    def _fill(self, at: datetime, key: str, reason: str, price: Decimal) -> None:
        qty = self.open_quantity      # the remaining quantity only, after any partial exits
        self._emit(Record(Event.EXIT_TRIGGER, at, key + ":trig", reason))
        self._emit(Record(Event.EXIT_FILL, at, key + ":fill", reason, price, qty))
        self.open_quantity -= qty
        self.pending = None
        self._emit(Record(Event.POSITION_CLOSED, at, key + ":closed", reason))

    def realized(self) -> Decimal:
        fills = [r for r in self.ledger if r.kind is Event.EXIT_FILL]
        total = Decimal(0)
        for f in fills:
            assert f.price is not None and f.quantity is not None
            diff = f.price - self.entry if self.long else self.entry - f.price
            total += diff * f.quantity
        return total
