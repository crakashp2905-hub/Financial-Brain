"""Positions, tax lots, P&L and reconciliation derived only from fills (FB-0231..0233, FB-0236, FB-0240, FB-0244 subset).

One truth: positions and cash are derived from journaled fills. Nothing here infers a position from a signal.

* FIFO and average-cost lot accounting, with realised P&L on each reduction.
* Gross vs net is explicit: ``unrealized`` is open gross, ``realized_net`` is closed net of fees.
* Settlement-date cash: proceeds are unsettled until their settlement day; restricted mode may not spend them.
* Futures: daily variation is (mark - previous mark) x quantity x multiplier, settled in cash.
* ``reconcile`` compares the fill-derived book with an independent expected set and returns exact exceptions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ZERO = Decimal(0)


class PortfolioError(ValueError):
    pass


@dataclass(frozen=True)
class FillRow:
    instrument: str
    side: str  # BUY or SELL
    quantity: int
    price: Decimal
    fees: Decimal
    trade_date: date
    settle_date: date


@dataclass
class Book:
    method: str = "FIFO"  # or AVERAGE
    lots: dict[str, deque] = field(default_factory=dict)
    realized_gross: Decimal = ZERO
    realized_fees: Decimal = ZERO
    unsettled: list[tuple[date, Decimal]] = field(default_factory=list)
    cash: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.method not in ("FIFO", "AVERAGE"):
            raise PortfolioError("method must be FIFO or AVERAGE")

    def position(self, instrument: str) -> int:
        return sum(q for q, _ in self.lots.get(instrument, deque()))

    def apply(self, f: FillRow) -> None:
        if f.quantity <= 0 or f.side not in ("BUY", "SELL"):
            raise PortfolioError("invalid fill")
        sign = 1 if f.side == "BUY" else -1
        notional = f.price * f.quantity
        self.cash += -sign * notional - f.fees
        self.realized_fees += f.fees
        self.unsettled.append((f.settle_date, -sign * notional))
        lots = self.lots.setdefault(f.instrument, deque())
        same_direction = not lots or (lots[0][0] > 0) == (sign > 0)
        if same_direction:
            if self.method == "AVERAGE" and lots:
                q0, p0 = lots[0]
                new_q = q0 + sign * f.quantity
                lots[0] = (new_q, (abs(q0) * p0 + f.quantity * f.price) / abs(new_q))
            else:
                lots.append((sign * f.quantity, f.price))
            return
        remaining = f.quantity
        while remaining and lots:
            lot_qty, lot_px = lots[0]
            take = min(abs(lot_qty), remaining)
            pnl_per_unit = (f.price - lot_px) if lot_qty > 0 else (lot_px - f.price)
            self.realized_gross += pnl_per_unit * take
            remaining -= take
            leftover = abs(lot_qty) - take
            if leftover == 0:
                lots.popleft()
            else:
                lots[0] = (leftover if lot_qty > 0 else -leftover, lot_px)
        if remaining:
            lots.append((sign * remaining, f.price))

    def settled_cash(self, on: date) -> Decimal:
        """Cash that has actually settled by ``on``. Restricted accounts may only spend this."""
        return self.cash - sum((amt for d, amt in self.unsettled if d > on), ZERO)

    def unrealized(self, marks: dict[str, Decimal], multipliers: dict[str, Decimal] | None = None) -> Decimal:
        total = ZERO
        for instrument, lots in self.lots.items():
            mark = marks.get(instrument)
            if mark is None:
                raise PortfolioError(f"no mark for {instrument}; refusing to value at zero")
            mult = (multipliers or {}).get(instrument, Decimal(1))
            for q, px in lots:
                total += (mark - px) * q * mult
        return total

    def realized_net(self) -> Decimal:
        return self.realized_gross - self.realized_fees


def futures_variation(prev_mark: Decimal, mark: Decimal, quantity: int, multiplier: Decimal) -> Decimal:
    """Daily variation cash flow for a futures position (positive = receive)."""
    return (mark - prev_mark) * Decimal(quantity) * multiplier


def reconcile(book_position: dict[str, int], expected_position: dict[str, int]) -> list[str]:
    """Exact exceptions between the fill-derived book and an independent expectation. Empty list = reconciled."""
    exceptions = []
    for k in sorted(set(book_position) | set(expected_position)):
        b, e = book_position.get(k, 0), expected_position.get(k, 0)
        if b != e:
            exceptions.append(f"{k}: book {b} vs expected {e}")
    return exceptions
