"""Paper order management (FB-0201, FB-0209..0214, FB-0215 subset, FB-0220, FB-0222).

* A closed transition table: any transition not listed is refused.
* Intents are idempotent. The same client order id with the same content is a no-op; with different content it is
  an error. A user action replayed twice creates at most one logical order.
* Cash is reserved before submission. Two orders cannot spend the same unreserved balance.
* A timeout or disconnect moves an order to UNKNOWN. An UNKNOWN order may not be resent; it must be reconciled.
* A fill that arrives after a cancel request is still recorded as a fill (cancel-fill race).
* ``kill_all`` cancels working orders; fills already recorded stay in the ledger.

Fill accounting (FE-0014, FE-0015, FE-0016, FE-0019):

* Every fill is validated completely before any field changes. A rejected fill leaves quantities, reservations, cash
  and state bit-identical.
* Settled cash moves with fills: a BUY spends ``price * qty``; a SELL adds ``price * qty``. Buying power is
  ``cash - reserved``, so a full buy reduces it.
* A partial fill releases only the reservation proportional to the filled quantity; the rest stays reserved.
* An optional ``provider_fill_id`` makes postbacks idempotent: the same id with the same content is a no-op, the same
  id with different content is refused.

This is the paper OMS. It never talks to a broker; there is no physical order path in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
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
FILLABLE = frozenset({State.SUBMITTED, State.ACCEPTED, State.PARTIAL, State.CANCEL_PENDING, State.UNKNOWN})

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

CENT = Decimal("0.01")


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
    reserved: Decimal  # reservation still held against this order
    state: State = State.CREATED
    filled: int = 0
    fills: list[tuple[int, Decimal]] = field(default_factory=list)
    initial_reserve: Decimal = Decimal(0)

    @property
    def remaining(self) -> int:
        return self.quantity - self.filled


class Oms:
    def __init__(self, cash: dict[str, Decimal]) -> None:
        self._cash = dict(cash)
        self._reserved: dict[str, Decimal] = {a: Decimal(0) for a in cash}
        self._orders: dict[str, Order] = {}
        self._intents: dict[str, tuple] = {}
        self._provider_fills: dict[tuple[str, str], tuple[int, Decimal]] = {}

    # --- cash ---
    def cash(self, account: str) -> Decimal:
        return self._cash[account]

    def reserved(self, account: str) -> Decimal:
        return self._reserved[account]

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
        order = Order(client_id, account, side, quantity, limit, reserve, initial_reserve=reserve)
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
        if new is State.FILLED and o.remaining != 0:
            raise OmsError(f"{client_id} cannot be FILLED with {o.remaining} units open")
        o.state = new
        if new in TERMINAL:
            self._release(o)
        return o

    def _release(self, o: Order) -> None:
        self._reserved[o.account] -= o.reserved
        o.reserved = Decimal(0)

    def record_fill(self, client_id: str, qty: int, price: Decimal, *,
                    provider_fill_id: str | None = None) -> Order:
        """Record a fill as a fact (cancel-fill race included). Validates everything before mutating anything."""
        if client_id not in self._orders:
            raise OmsError(f"unknown order {client_id}")
        o = self._orders[client_id]
        if provider_fill_id is not None:
            prior = self._provider_fills.get((client_id, provider_fill_id))
            if prior is not None:
                if prior != (qty, price):
                    raise OmsError(f"provider fill id {provider_fill_id} reused with different content")
                return o  # duplicate postback: the economic fill already exists exactly once
        # ---- validation: no mutation below this line until the checks pass ----
        if type(qty) is not int or qty <= 0:
            raise OmsError("fill quantity must be a positive integer")
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise OmsError("fill price must be a positive finite decimal")
        if qty > o.remaining:
            raise OmsError("fill quantity out of range for remaining order")
        if o.state not in FILLABLE:
            raise OmsError(f"cannot fill order in state {o.state.value}")
        if o.limit is not None and ((o.side == "BUY" and price > o.limit) or (o.side == "SELL" and price < o.limit)):
            raise OmsError("fill price violates the order limit")
        remaining_after = o.remaining - qty
        if remaining_after == 0 and o.state is not State.CANCEL_PENDING and State.FILLED not in TRANSITIONS[o.state]:
            raise OmsError(f"cannot fill order in state {o.state.value}")
        notional = price * qty
        if remaining_after == 0:
            release = o.reserved
        else:
            release = (o.reserved * qty / Decimal(o.remaining)).quantize(CENT, rounding=ROUND_HALF_EVEN)
        if release > o.reserved:
            raise OmsError("internal reservation invariant violated")
        if o.side == "BUY" and notional > self._cash[o.account] - self._reserved[o.account] + release:
            raise InsufficientCash("fill would overspend settled cash")
        # ---- mutation ----
        o.filled += qty
        o.fills.append((qty, price))
        o.reserved -= release
        self._reserved[o.account] -= release
        self._cash[o.account] += -notional if o.side == "BUY" else notional
        if provider_fill_id is not None:
            self._provider_fills[(client_id, provider_fill_id)] = (qty, price)
        if o.state is State.CANCEL_PENDING:
            # the cancel has not yet been confirmed; keep the fill and let the cancel resolve afterwards
            if remaining_after == 0:
                o.state = State.FILLED
                self._release(o)
            return o
        if remaining_after == 0:
            o.state = State.FILLED
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
