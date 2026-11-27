"""What the OMS needs from a broker, and a paper broker that behaves like one.

A broker here is five calls. Every snapshot is the broker's view, in the broker's words mapped to ours; the OMS
never trusts its own idea of an order over the broker's.

    place(order)        -> broker order id (raises BrokerTimeout when the outcome is unknown)
    cancel(broker_id)
    orders()            -> [BrokerOrder]   every order of the session, with its tag
    positions()         -> {(exchange, symbol): net quantity}   holdings plus today's net
    ltp(symbols)        -> {symbol: last price}

``PaperBroker`` keeps orders in memory and fills them against prices it is fed (``tick``): a buy limit fills when the
price is at or below it, a sell when at or above, up to ``max_fill`` shares per tick - so partial fills, rejects and
timeouts can all be produced on purpose, which is how the OMS's paths are tested.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from . import model as M


class BrokerError(RuntimeError):
    """The broker refused the request (the order does not exist at the broker)."""


class BrokerTimeout(RuntimeError):
    """No answer: the order may or may not exist at the broker. The OMS marks it UNKNOWN and looks it up by tag."""


@dataclass(frozen=True)
class BrokerOrder:
    broker_id: str
    tag: str | None
    symbol: str
    exchange: str
    side: str
    qty: int
    filled_qty: int
    avg_price: float | None
    state: str                     #: one of model.BROKER_STATES
    message: str | None = None
    updated: str | None = None     #: the broker's own timestamp for this version of the order


@dataclass(frozen=True)
class QuoteSnapshot:
    price: float
    exchange_at: datetime | None
    received_at: datetime | None
    source: str


class Broker(Protocol):
    name: str

    def place(self, order: M.Order) -> str: ...
    def cancel(self, broker_id: str) -> None: ...
    def orders(self) -> list[BrokerOrder]: ...
    def positions(self) -> dict[tuple[str, str], int]: ...
    def ltp(self, symbols: list[tuple[str, str]]) -> dict[tuple[str, str], float]: ...
    def quote_snapshot(self, symbols: list[tuple[str, str]]) -> dict[tuple[str, str], QuoteSnapshot]: ...


@dataclass
class PaperBroker:
    name: str = "paper"
    max_fill: int | None = None                       #: shares per tick; None fills the whole order at once
    reject_symbols: set[str] = field(default_factory=set)
    timeout_next: int = 0                             #: the next n placements time out *after* being accepted
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _orders: dict[str, dict] = field(default_factory=dict)
    _px: dict[tuple[str, str], float] = field(default_factory=dict)
    _quote_time: dict[tuple[str, str], datetime | None] = field(default_factory=dict)
    _quote_received_at: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _quote_source: dict[tuple[str, str], str] = field(default_factory=dict)
    _pos: dict[tuple[str, str], int] = field(default_factory=dict)
    _clock: itertools.count = field(default_factory=lambda: itertools.count(1))

    def place(self, order: M.Order) -> str:
        bid = f"P{next(self._ids):08d}"
        state = M.REJECTED if order.symbol in self.reject_symbols else M.OPEN
        self._orders[bid] = {"o": order, "filled": 0, "avg": None, "state": state,
                             "msg": "symbol rejected by paper broker" if state == M.REJECTED else None,
                             "upd": next(self._clock)}
        self._match(bid)
        if self.timeout_next > 0:                     # accepted and processed; the reply is what was lost
            self.timeout_next -= 1
            raise BrokerTimeout("paper broker: simulated timeout after acceptance")
        return bid

    def cancel(self, broker_id: str) -> None:
        r = self._orders.get(broker_id)
        if r is None:
            raise BrokerError(f"no order {broker_id}")
        if r["state"] in (M.OPEN, M.PARTIAL):
            r["state"], r["upd"] = M.CANCELLED, next(self._clock)

    def tick(self, prices: dict[tuple[str, str], float], *, observed_at: datetime | None = None,
             exchange_timestamps: dict[tuple[str, str], datetime | None] | None = None,
             source: str = "PAPER_SIMULATION") -> None:
        observed_at = observed_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None or not source.strip():
            raise ValueError("paper quote receipt timestamp and source must be valid")
        self._px.update(prices)
        exchange_timestamps = exchange_timestamps or {}
        for key in prices:
            exchange_at = exchange_timestamps.get(key, observed_at)
            if exchange_at is not None and (exchange_at.tzinfo is None or exchange_at.utcoffset() is None):
                raise ValueError("paper quote exchange timestamp must be timezone aware")
            self._quote_time[key] = exchange_at
            self._quote_received_at[key] = observed_at
            self._quote_source[key] = source
        for bid in list(self._orders):
            self._match(bid)

    def _match(self, bid: str) -> None:
        r = self._orders[bid]
        o = r["o"]
        px = self._px.get((o.exchange, o.symbol))
        if r["state"] not in (M.OPEN, M.PARTIAL) or px is None:
            return
        crosses = px <= o.limit_price if o.side == M.BUY else px >= o.limit_price
        if not crosses:
            return
        left = o.qty - r["filled"]
        q = left if self.max_fill is None else min(left, self.max_fill)
        fill_px = min(px, o.limit_price) if o.side == M.BUY else max(px, o.limit_price)
        r["avg"] = ((r["avg"] or 0.0) * r["filled"] + fill_px * q) / (r["filled"] + q)
        r["filled"] += q
        r["state"] = M.FILLED if r["filled"] == o.qty else M.PARTIAL
        r["upd"] = next(self._clock)
        k = (o.exchange, o.symbol)
        self._pos[k] = self._pos.get(k, 0) + (q if o.side == M.BUY else -q)

    def orders(self) -> list[BrokerOrder]:
        return [BrokerOrder(bid, r["o"].client_id, r["o"].symbol, r["o"].exchange, r["o"].side, r["o"].qty, r["filled"],
                            r["avg"], r["state"], r["msg"], str(r["upd"])) for bid, r in self._orders.items()]

    def positions(self) -> dict[tuple[str, str], int]:
        return {k: v for k, v in self._pos.items() if v}

    def ltp(self, symbols):
        return {k: self._px[k] for k in symbols if k in self._px}

    def quote_snapshot(self, symbols):
        return {key: QuoteSnapshot(self._px[key], self._quote_time.get(key),
                                   self._quote_received_at.get(key),
                                   self._quote_source.get(key, "PAPER_SIMULATION"))
                for key in symbols if key in self._px}
