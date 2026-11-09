"""Event schemas (FB-0045 market data, FB-0046 research/order/fill/position).

All schemas are frozen dataclasses with validation in ``__post_init__``. Times must be timezone-aware UTC. Quantities
and prices are Decimal and must be positive where the domain requires it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


def _utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{name} must be a timezone-aware UTC instant")


def _positive(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or value <= 0:
        raise ValueError(f"{name} must be a positive Decimal")


@dataclass(frozen=True)
class Trade:
    instrument: str
    time: datetime
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        _utc("time", self.time)
        _positive("price", self.price)
        _positive("size", self.size)


@dataclass(frozen=True)
class Quote:
    instrument: str
    time: datetime
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        _utc("time", self.time)
        _positive("bid", self.bid)
        _positive("ask", self.ask)
        if self.bid > self.ask:
            raise ValueError("crossed quote: bid above ask")


@dataclass(frozen=True)
class BookSnapshot:
    instrument: str
    time: datetime
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]

    def __post_init__(self) -> None:
        _utc("time", self.time)
        for price, size in self.bids + self.asks:
            _positive("level price", price)
            _positive("level size", size)
        if self.bids and self.asks and self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("crossed book")


@dataclass(frozen=True)
class BarEvent:
    instrument: str
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _utc("time", self.time)
        if not (self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close)):
            raise ValueError("bar high/low do not bracket open and close")
        if self.volume < 0:
            raise ValueError("negative volume")


@dataclass(frozen=True)
class CorporateAction:
    instrument: str
    effective: datetime
    kind: str  # split | bonus | rights | dividend | demerger
    ratio: Decimal

    def __post_init__(self) -> None:
        _utc("effective", self.effective)
        if self.kind not in {"split", "bonus", "rights", "dividend", "demerger"}:
            raise ValueError(f"unknown corporate action kind {self.kind!r}")
        _positive("ratio", self.ratio)


MarketDataEvent = Trade | Quote | BookSnapshot | BarEvent | CorporateAction


@dataclass(frozen=True)
class Signal:
    strategy: str
    instrument: str
    time: datetime
    direction: int  # -1, 0, +1

    def __post_init__(self) -> None:
        _utc("time", self.time)
        if self.direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0 or +1")


@dataclass(frozen=True)
class Intent:
    strategy: str
    instrument: str
    time: datetime
    side: str  # BUY | SELL
    quantity: Decimal

    def __post_init__(self) -> None:
        _utc("time", self.time)
        if self.side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        _positive("quantity", self.quantity)


@dataclass(frozen=True)
class Order:
    client_id: str
    instrument: str
    time: datetime
    side: str
    quantity: Decimal
    limit: Decimal | None = None

    def __post_init__(self) -> None:
        _utc("time", self.time)
        if not self.client_id:
            raise ValueError("client_id required for idempotency")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        _positive("quantity", self.quantity)
        if self.limit is not None:
            _positive("limit", self.limit)


@dataclass(frozen=True)
class Fill:
    client_id: str
    time: datetime
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _utc("time", self.time)
        _positive("price", self.price)
        _positive("quantity", self.quantity)


@dataclass(frozen=True)
class Position:
    instrument: str
    time: datetime
    quantity: Decimal  # signed: negative is short

    def __post_init__(self) -> None:
        _utc("time", self.time)
