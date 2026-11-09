"""Paper order management (FB-0201, FB-0209..0214, FB-0215 subset, FB-0220, FB-0222).

* A closed transition table: any transition not listed is refused.
* Intents are idempotent. The same client order id with the same content is a no-op; with different content it is
  an error. A user action replayed twice creates at most one logical order.
* Cash is reserved before submission. Two orders cannot spend the same unreserved balance.
* A timeout or disconnect moves an order to UNKNOWN. An UNKNOWN order may not be resent; it must be reconciled.
* A fill that arrives after a cancel request is still recorded as a fill (cancel-fill race).
* ``kill_all`` cancels working orders; fills already recorded stay in the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class State(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"


TERMINAL = frozenset({State.FILLED, State.CANCELED, State.REJECTED, State.EXPIRED})

TRANSITIONS: dict[State, frozenset[State]] = {
    State.CREATED: frozenset({State.SUBMITTED, State.REJECTED}),
    State.SUBMITTED: frozenset({State.ACCEPTED, State.REJECTED, State.UNKNOWN, State.FILLED, State.PARTIAL}),
    State.ACCEPTED: frozenset({State.PARTIAL, State.FILLED, State.CANCEL_PENDING, State.EXPIRED, State.UNKNOWN}),
    State.PARTIAL: frozenset({State.PARTIAL, State.FILLED, State.CANCEL_PENDING, State.EXPIRED}),
    State.CANCEL_PENDING: frozenset({State.CANCELED, State.FILLED, State.PARTIAL}),
    State.UNKNOWN: frozenset({State.ACCEPTED, State.FILLED, State.PARTIAL, State.REJECTED, State.CANCELED}),
    State.FILLED: frozenset(),
    State.CANCELED: frozenset(),
    State.REJECTED: frozenset(),
    State.EXPIRED: frozenset(),
}


class OmsError(ValueError):
    pass


class InsufficientCash(OmsError):
    pass


@dataclass
class Order:
    client_id: str
    account: str
    side: str
    quantity: int
    limit: Decimal | None
    reserved: Decimal
    state: State = State.CREATED
    filled: int = 0
    fills: list[tuple[int, Decimal]] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.quantity - self.filled


class Oms:
    def __init__(self, cash: dict[str, Decimal]) -> None:
        self._cash = dict(cash)
        self._reserved: dict[str, Decimal] = {a: Decimal(0) for a in cash}
        self._orders: dict[str, Order] = {}
        self._intents: dict[str, tuple] = {}

    # --- cash ---
    def available(self, account: str) -> Decimal:
        return self._cash[account] - self._reserved[account]

    # --- intents ---
    def submit_intent(
        self, *, client_id: str, account: str, side: str, quantity: int,
        limit: Decimal | None, reserve: Decimal,
    ) -> Order:
        if side not in ("BUY", "SELL") or quantity <= 0 or reserve < 0:
            raise OmsError("invalid intent")
        content = (account, side, quantity, limit, reserve)
        if client_id in self._intents:
            if self._intents[client_id] != content:
                raise OmsError(f"client id {client_id} reused with different content")
            return self._orders[client_id]
        if account not in self._cash:
            raise OmsError(f"unknown account {account}")
        if reserve > self.available(account):
            raise InsufficientCash(f"needs {reserve}, available {self.available(account)}")
        self._reserved[account] += reserve
        order = Order(client_id, account, side, quantity, limit, reserve)
        self._orders[client_id] = order
        self._intents[client_id] = content
        return order

    def order(self, client_id: str) -> Order:
        return self._orders[client_id]

    # --- transitions ---
    def move(self, client_id: str, new: State) -> Order:
        o = self._orders[client_id]
        if new not in TRANSITIONS[o.state]:
            raise OmsError(f"illegal transition {o.state.value} -> {new.value} for {client_id}")
        o.state = new
        if new in TERMINAL:
            self._release(o)
        return o

    def _release(self, o: Order) -> None:
        self._reserved[o.account] -= o.reserved
        o.reserved = Decimal(0)

    def record_fill(self, client_id: str, qty: int, price: Decimal) -> Order:
        """Fills are facts: they are recorded even after a cancel request (cancel-fill race)."""
        o = self._orders[client_id]
        if qty <= 0 or qty > o.remaining:
            raise OmsError("fill quantity out of range for remaining order")
        o.filled += qty
        o.fills.append((qty, price))
        if o.state is State.CANCEL_PENDING:
            # the cancel has not yet been confirmed; keep the fill and let the cancel resolve afterwards
            if o.remaining == 0:
                o.state = State.FILLED
                self._release(o)
            return o
        if o.remaining == 0:
            if State.FILLED in TRANSITIONS[o.state]:
                o.state = State.FILLED
            else:
                raise OmsError(f"cannot fill order in state {o.state.value}")
            self._release(o)
        elif o.state in (State.ACCEPTED, State.SUBMITTED, State.UNKNOWN, State.PARTIAL):
            o.state = State.PARTIAL
        return o

    def resend_allowed(self, client_id: str) -> bool:
        """Never resend a possibly-executed order; an UNKNOWN order must be reconciled first."""
        return self._orders[client_id].state is State.CREATED

    def kill_all(self) -> list[str]:
        """Emergency: cancel every working order. Already recorded fills are left intact."""
        cancelled = []
        for cid, o in sorted(self._orders.items()):
            if o.state in TERMINAL:
                continue
            if State.CANCEL_PENDING in TRANSITIONS[o.state]:
                self.move(cid, State.CANCEL_PENDING)
                cancelled.append(cid)
        return cancelled
