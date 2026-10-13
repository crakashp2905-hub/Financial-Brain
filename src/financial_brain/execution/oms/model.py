"""An order, and the only transitions it may take.

    NEW -> REJECTED_RISK                          the pre-trade gate refused it (terminal)
    NEW -> AWAITING_APPROVAL -> APPROVED          stage TICKET: a human approves each order
    NEW -> APPROVED                               stages SMALL_AUTO / SCALED
    AWAITING_APPROVAL -> CANCELLED                a human declined it, or the session ended
    APPROVED -> SUBMITTED                         the broker acknowledged it with an order id
    APPROVED -> UNKNOWN                           the request may or may not have reached the broker
    APPROVED -> REJECTED | CANCELLED              the broker refused it, or a lock closed before it was sent
    SUBMITTED / UNKNOWN -> OPEN | PARTIAL | FILLED | REJECTED | CANCELLED | NOT_FOUND
    OPEN -> PARTIAL | FILLED | CANCELLED | REJECTED | EXPIRED
    PARTIAL -> PARTIAL (more filled) | FILLED | CANCELLED | EXPIRED

**UNKNOWN is the state that makes this safe.** A timeout on a placement does not say whether the order exists. The
order's client id travels to the broker as its ``tag``, so the next poll finds it by tag; an order is never re-sent
because a request timed out. ``NOT_FOUND`` (the broker has no order with that tag) is terminal for that id - a
retry is a new order with a new id, created deliberately.

A fill may only grow: ``filled_qty`` never decreases and never exceeds ``qty``. A cancelled order keeps what it
filled.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace

NEW, REJECTED_RISK, AWAITING_APPROVAL, APPROVED = "NEW", "REJECTED_RISK", "AWAITING_APPROVAL", "APPROVED"
SUBMITTED, UNKNOWN, OPEN, PARTIAL = "SUBMITTED", "UNKNOWN", "OPEN", "PARTIAL"
FILLED, REJECTED, CANCELLED, EXPIRED, NOT_FOUND = "FILLED", "REJECTED", "CANCELLED", "EXPIRED", "NOT_FOUND"

BROKER_STATES = {OPEN, PARTIAL, FILLED, REJECTED, CANCELLED, EXPIRED}
TRANSITIONS: dict[str, set[str]] = {
    NEW: {REJECTED_RISK, AWAITING_APPROVAL, APPROVED},
    AWAITING_APPROVAL: {APPROVED, CANCELLED},
    APPROVED: {SUBMITTED, UNKNOWN, REJECTED, CANCELLED},
    SUBMITTED: BROKER_STATES,
    UNKNOWN: BROKER_STATES | {NOT_FOUND},
    OPEN: {PARTIAL, FILLED, CANCELLED, REJECTED, EXPIRED},
    PARTIAL: {PARTIAL, FILLED, CANCELLED, EXPIRED},
}
TERMINAL = {REJECTED_RISK, FILLED, REJECTED, CANCELLED, EXPIRED, NOT_FOUND}
LIVE = {SUBMITTED, UNKNOWN, OPEN, PARTIAL}             #: may still fill at the broker

BUY, SELL = "BUY", "SELL"


class OrderError(ValueError):
    pass


def client_id(strategy: str, day: str, symbol: str, side: str, qty: int, slice_no: int = 0, attempt: int = 0) -> str:
    """Deterministic, 16 characters (Kite's tag takes 20): the same intent always gets the same id, so a second
    submission of the same order is recognised as the first."""
    raw = f"{strategy}|{day}|{symbol}|{side}|{qty}|{slice_no}|{attempt}"
    return "fb" + hashlib.sha1(raw.encode()).hexdigest()[:14]


@dataclass(frozen=True)
class Order:
    client_id: str
    strategy: str
    day: str
    symbol: str
    exchange: str
    side: str
    qty: int
    limit_price: float
    product: str = "CNC"
    parent_id: str | None = None
    state: str = NEW
    broker_id: str | None = None
    filled_qty: int = 0
    avg_price: float | None = None
    arrival_price: float | None = None           #: the price when the decision was taken (for shortfall)
    reason: str | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.side not in (BUY, SELL):
            raise OrderError(f"side {self.side!r}")
        if not isinstance(self.qty, int) or self.qty <= 0:
            raise OrderError(f"quantity must be a positive whole number, got {self.qty!r}")
        if not (self.limit_price > 0):
            raise OrderError("limit orders only: a positive limit price is required")
        if not 0 <= self.filled_qty <= self.qty:
            raise OrderError(f"filled {self.filled_qty} of {self.qty}")

    @property
    def notional(self) -> float:
        return self.qty * self.limit_price

    @property
    def signed_filled(self) -> int:
        return self.filled_qty if self.side == BUY else -self.filled_qty

    def as_dict(self) -> dict:
        return asdict(self)


def advance(o: Order, to: str, **changes) -> Order:
    """The only way an order changes state."""
    if o.state in TERMINAL:
        raise OrderError(f"{o.client_id} is {o.state}: terminal")
    if to not in TRANSITIONS.get(o.state, set()):
        raise OrderError(f"{o.client_id}: {o.state} -> {to} is not a legal transition")
    filled = changes.get("filled_qty", o.filled_qty)
    if filled < o.filled_qty:
        raise OrderError(f"{o.client_id}: filled quantity cannot fall ({o.filled_qty} -> {filled})")
    if to == FILLED and filled != o.qty:
        filled = o.qty if "filled_qty" not in changes else filled
        if filled != o.qty:
            raise OrderError(f"{o.client_id}: FILLED with {filled} of {o.qty}")
        changes["filled_qty"] = filled
    if to == PARTIAL and not 0 < filled < o.qty:
        raise OrderError(f"{o.client_id}: PARTIAL needs 0 < filled < {o.qty}, got {filled}")
    if o.state == PARTIAL and to == PARTIAL and filled == o.filled_qty:
        return o                                          # nothing new: not a transition
    return replace(o, state=to, **changes)
