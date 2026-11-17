"""Central capital allocator: reserve before any desk places a paper order (checklist section 17).

Reservations are atomic (one lock), keyed and idempotent, and checked against desk, underlying and
total limits. A desk can be quarantined; a daily-loss breach trips a kill switch that only a human
reset clears, and nothing here raises limits to recover losses.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal


class AllocationError(ValueError):
    pass


@dataclass
class CapitalAllocator:
    capital: Decimal
    desk_limits: dict[str, Decimal]
    underlying_limits: dict[str, Decimal] = field(default_factory=dict)
    daily_loss_limit: Decimal | None = None
    reservations: dict[str, tuple[str, str, Decimal]] = field(default_factory=dict)
    quarantined: set[str] = field(default_factory=set)
    killed: bool = False
    realized_today: Decimal = Decimal(0)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _used(self, *, desk: str | None = None, underlying: str | None = None) -> Decimal:
        return sum((amt for d, u, amt in self.reservations.values()
                    if (desk is None or d == desk) and (underlying is None or u == underlying)),
                   Decimal(0))

    @property
    def available(self) -> Decimal:
        return self.capital - self._used()

    def reserve(self, key: str, desk: str, underlying: str, amount: Decimal) -> bool:
        """Grant or deny a reservation. Returns True when granted (or already granted for key)."""
        if amount <= 0:
            raise AllocationError("amount must be positive")
        with self._lock:
            if key in self.reservations:
                return self.reservations[key] == (desk, underlying, amount)
            if self.killed or desk in self.quarantined:
                return False
            if amount > self.available:
                return False
            if self._used(desk=desk) + amount > self.desk_limits.get(desk, Decimal(0)):
                return False
            lim = self.underlying_limits.get(underlying)
            if lim is not None and self._used(underlying=underlying) + amount > lim:
                return False
            self.reservations[key] = (desk, underlying, amount)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self.reservations.pop(key, None)

    def record_pnl(self, pnl: Decimal) -> None:
        with self._lock:
            self.realized_today += pnl
            if self.daily_loss_limit is not None and -self.realized_today >= self.daily_loss_limit:
                self.killed = True

    def quarantine(self, desk: str) -> None:
        self.quarantined.add(desk)

    def human_reset(self, *, confirmed_by: str) -> None:
        """Only a named human clears the kill switch; limits are not changed."""
        if not confirmed_by:
            raise AllocationError("a human must confirm the reset")
        with self._lock:
            self.killed = False
            self.realized_today = Decimal(0)
