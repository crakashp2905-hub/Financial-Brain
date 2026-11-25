"""Shared exchange paper OMS for equities, ETFs and options (checklist section 8).

Orders are filled only against a quote the strategy could have seen (``instruments.pit.quote_at``),
at the ask for a buy and the bid for a sell, limited by the displayed size times a participation cap.
A missing or stale quote leaves the order unfilled and, for a stale feed, halts the desk. A signal that
has expired never fills on a late quote. Each signal executes at most once per account, and automatic
and trader-approved accounts never share holdings. Every fill carries a fidelity tag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import Enum

from financial_brain.instruments.pit import Quote, fill_reference, quote_at


class OmsError(ValueError):
    pass


class OrderStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    MISSED = "MISSED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


class Fidelity(str, Enum):
    OBSERVED_QUOTE_SIM = "OBSERVED_QUOTE_SIM"
    BAR_PROXY_SIM = "BAR_PROXY_SIM"


class Account(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    TRADER_APPROVED = "TRADER_APPROVED"


#: Option structures permitted in paper (FB-0808). Anything else is blocked.
ALLOWED_OPTION_STRUCTURES = frozenset({"LONG_CALL", "LONG_PUT", "DEBIT_CALL_SPREAD", "DEBIT_PUT_SPREAD"})


def option_structure_allowed(structure: str) -> bool:
    return structure in ALLOWED_OPTION_STRUCTURES


@dataclass(frozen=True)
class Fill:
    order_id: str
    at: datetime
    units: int
    price: Decimal
    fidelity: Fidelity
    quote_ts: datetime


@dataclass
class PaperOrder:
    order_id: str
    signal_id: str
    account: Account
    desk: str
    instrument: str
    side: str
    units: int
    limit: Decimal
    expires_at: datetime
    lot_size: int = 1
    status: OrderStatus = OrderStatus.ACCEPTED
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise OmsError("side must be BUY or SELL")
        if self.units <= 0 or self.units % self.lot_size:
            raise OmsError("units must be a positive whole number of lots (FB-0802)")

    @property
    def filled(self) -> int:
        return sum(f.units for f in self.fills)

    @property
    def remaining(self) -> int:
        return self.units - self.filled


@dataclass
class ExchangePaperOms:
    max_quote_age: timedelta
    participation: Decimal = Decimal("0.1")
    orders: dict[str, PaperOrder] = field(default_factory=dict)
    halted: set[str] = field(default_factory=set)
    _executed: set[tuple[str, Account]] = field(default_factory=set)

    def submit(self, order: PaperOrder) -> PaperOrder:
        """One execution per signal per account (FB-0810, FB-0812)."""
        key = (order.signal_id, order.account)
        if key in self._executed:
            raise OmsError(f"signal {order.signal_id} already executed in {order.account.value}")
        if order.desk in self.halted:
            order.status = OrderStatus.REJECTED
        else:
            order.status = OrderStatus.WORKING
            self._executed.add(key)
        self.orders[order.order_id] = order
        return order

    def on_quote(self, order_id: str, quote: Quote | None, now: datetime) -> Fill | None:
        o = self.orders[order_id]
        if o.status not in (OrderStatus.WORKING, OrderStatus.PARTIAL):
            return None
        if now >= o.expires_at:
            # unfilled remainder is cancelled; a never-filled order is missed
            o.status = OrderStatus.CANCELLED if o.fills else OrderStatus.MISSED
            return None
        pricing = quote_at(quote, now, max_age=self.max_quote_age)
        if not pricing.ok or quote is None:
            if quote is not None and any(r.startswith("quote older") for r in pricing.reasons):
                self.halted.add(o.desk)   # stale live feed halts the desk (FB-0811)
            return None
        price = fill_reference(quote, o.side)
        if (o.side == "BUY" and price > o.limit) or (o.side == "SELL" and price < o.limit):
            return None
        size = quote.ask_qty if o.side == "BUY" else quote.bid_qty
        cap = int((Decimal(size) * self.participation).to_integral_value(rounding=ROUND_DOWN))
        cap -= cap % o.lot_size
        qty = min(o.remaining, cap)
        if qty <= 0:
            return None
        f = Fill(o.order_id, now, qty, price, Fidelity.OBSERVED_QUOTE_SIM, quote.exchange_ts)
        o.fills.append(f)
        o.status = OrderStatus.FILLED if o.remaining == 0 else OrderStatus.PARTIAL
        return f

    def timeout(self, order_id: str, now: datetime, ack_deadline: datetime) -> OrderStatus:
        """No acknowledgement by the deadline: TIMED_OUT; a working order with an unknown venue
        outcome is UNKNOWN, never assumed filled or cancelled (FB-0804)."""
        o = self.orders[order_id]
        if o.status in (OrderStatus.WORKING, OrderStatus.PARTIAL) and now >= ack_deadline:
            o.status = OrderStatus.UNKNOWN if o.fills else OrderStatus.TIMED_OUT
        return o.status

    def resume(self, desk: str) -> None:
        self.halted.discard(desk)
