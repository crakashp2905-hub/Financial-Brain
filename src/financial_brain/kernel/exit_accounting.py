"""Deterministic trailing-stop and staged-exit accounting primitives.

These functions are intentionally independent of any broker or runner. They account
for filled quantities only; callers must supply executable fill prices and persist
their own event receipts.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class ExitAccountingError(ValueError):
    """Exit accounting input violates quantity, price or policy invariants."""


def _positive(name: str, value: Decimal | str | int | float) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ExitAccountingError(f"{name} must be decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ExitAccountingError(f"{name} must be finite and positive")
    return result


@dataclass(frozen=True, slots=True)
class TrailingStopState:
    """Monotone trailing stop for one position and one frozen policy version."""

    side: str
    policy_version: str
    distance: Decimal
    watermark: Decimal
    stop_price: Decimal

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL_SHORT"}:
            raise ExitAccountingError("side must be BUY or SELL_SHORT")
        if not self.policy_version.strip():
            raise ExitAccountingError("policy_version is required")
        for name in ("distance", "watermark", "stop_price"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        expected = (self.watermark - self.distance if self.side == "BUY"
                    else self.watermark + self.distance)
        if self.stop_price != expected:
            raise ExitAccountingError("stop price must match watermark and fixed trailing distance")

    @classmethod
    def start(cls, *, side: str, policy_version: str,
              distance: Decimal | str | int | float,
              entry_price: Decimal | str | int | float) -> "TrailingStopState":
        gap = _positive("distance", distance)
        entry = _positive("entry_price", entry_price)
        stop = entry - gap if side == "BUY" else entry + gap
        if stop <= 0:
            raise ExitAccountingError("initial trailing stop must remain positive")
        return cls(side, policy_version, gap, entry, stop)

    def observe(self, price: Decimal | str | int | float, *, policy_version: str) -> "TrailingStopState":
        """Advance watermark; a changed policy version explicitly starts a new trail."""
        current = _positive("price", price)
        if not policy_version.strip():
            raise ExitAccountingError("policy_version is required")
        if policy_version != self.policy_version:
            return self.start(side=self.side, policy_version=policy_version,
                              distance=self.distance, entry_price=current)
        watermark = max(self.watermark, current) if self.side == "BUY" else min(self.watermark, current)
        stop = watermark - self.distance if self.side == "BUY" else watermark + self.distance
        if stop <= 0:
            raise ExitAccountingError("trailing stop must remain positive")
        return TrailingStopState(self.side, self.policy_version, self.distance, watermark, stop)


@dataclass(frozen=True, slots=True)
class ExitFill:
    event_id: str
    quantity: Decimal
    price: Decimal
    costs: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ExitAccountingError("event_id is required")
        for name in ("quantity", "price"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        try:
            costs = Decimal(str(self.costs))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ExitAccountingError("costs must be decimal") from exc
        if not costs.is_finite() or costs < 0:
            raise ExitAccountingError("costs must be finite and non-negative")
        object.__setattr__(self, "costs", costs)


@dataclass(frozen=True, slots=True)
class PositionExitLedger:
    """Cumulative realized accounting for entry inventory and exit fill events."""

    side: str
    entry_price: Decimal
    initial_quantity: Decimal
    fills: tuple[ExitFill, ...] = ()

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL_SHORT"}:
            raise ExitAccountingError("side must be BUY or SELL_SHORT")
        object.__setattr__(self, "entry_price", _positive("entry_price", self.entry_price))
        object.__setattr__(self, "initial_quantity", _positive("initial_quantity", self.initial_quantity))
        if any(not isinstance(fill, ExitFill) for fill in self.fills):
            raise ExitAccountingError("fills must be typed ExitFill records")
        ids = [fill.event_id for fill in self.fills]
        if len(ids) != len(set(ids)):
            raise ExitAccountingError("duplicate exit event IDs are not idempotent")
        if sum((fill.quantity for fill in self.fills), Decimal(0)) > self.initial_quantity:
            raise ExitAccountingError("exit fills cannot exceed the open position")

    @property
    def exited_quantity(self) -> Decimal:
        return sum((fill.quantity for fill in self.fills), Decimal(0))

    @property
    def remaining_quantity(self) -> Decimal:
        return self.initial_quantity - self.exited_quantity

    @property
    def realized_pnl(self) -> Decimal:
        sign = Decimal(1) if self.side == "BUY" else Decimal(-1)
        return sum((sign * (fill.price - self.entry_price) * fill.quantity - fill.costs
                    for fill in self.fills), Decimal(0))

    def apply(self, fill: ExitFill) -> "PositionExitLedger":
        """Append one unique fill; exact event replay is idempotent."""
        prior = next((row for row in self.fills if row.event_id == fill.event_id), None)
        if prior is not None:
            if prior != fill:
                raise ExitAccountingError("event ID replay conflicts with recorded fill")
            return self
        return PositionExitLedger(self.side, self.entry_price, self.initial_quantity,
                                  self.fills + (fill,))

    def marked_total_pnl(self, mark: Decimal | str | int | float) -> Decimal:
        mark_price = _positive("mark", mark)
        sign = Decimal(1) if self.side == "BUY" else Decimal(-1)
        unrealized = sign * (mark_price - self.entry_price) * self.remaining_quantity
        return self.realized_pnl + unrealized


__all__ = ["ExitAccountingError", "ExitFill", "PositionExitLedger", "TrailingStopState"]
