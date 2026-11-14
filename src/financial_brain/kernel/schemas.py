"""Event schemas (FB-0045 market data, FB-0046 research/order/fill/position).

All schemas are frozen dataclasses with validation in ``__post_init__``. Times must be timezone-aware UTC. Quantities
and prices are Decimal and must be positive where the domain requires it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import re


def _utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{name} must be a timezone-aware UTC instant")


def _positive(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive Decimal")


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


class EventSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
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


def _sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


@dataclass(frozen=True)
class InstrumentRef:
    """Asset-class-aware contract identity shared by paper engines and event adapters."""

    venue: str
    symbol: str
    asset_class: AssetClass
    lot_size: int = 1
    expiry: date | None = None
    multiplier: Decimal = Decimal(1)
    strike: Decimal | None = None
    option_right: str | None = None

    def __post_init__(self) -> None:
        if not self.venue.strip() or not self.symbol.strip():
            raise ValueError("instrument venue and symbol are required")
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError("asset_class must be an AssetClass value")
        if type(self.lot_size) is not int:
            raise ValueError("instrument lot_size must be an integer")
        if self.lot_size < 1:
            raise ValueError("instrument lot_size must be positive")
        _positive("multiplier", self.multiplier)
        if self.asset_class in {AssetClass.EQUITY, AssetClass.ETF}:
            if self.expiry is not None or self.strike is not None or self.option_right is not None:
                raise ValueError("cash instruments cannot carry derivative contract fields")
        elif self.asset_class is AssetClass.FUTURE:
            if self.expiry is None or self.strike is not None or self.option_right is not None:
                raise ValueError("futures require expiry and cannot carry option fields")
        elif self.asset_class is AssetClass.OPTION:
            if self.expiry is None or self.strike is None or self.option_right not in {"CE", "PE"}:
                raise ValueError("options require expiry, positive strike and CE/PE right")
            _positive("strike", self.strike)

    @property
    def key(self) -> str:
        suffix = f":{self.expiry.isoformat()}" if self.expiry else ""
        if self.asset_class is AssetClass.OPTION:
            suffix += f":{self.strike}:{self.option_right}"
        return f"{self.venue}:{self.asset_class.value}:{self.symbol}{suffix}"


@dataclass(frozen=True)
class IntentEvent:
    event_id: str
    order_id: str
    account_id: str
    strategy_id: str
    instrument: InstrumentRef
    side: EventSide
    quantity: int
    occurred_at: datetime
    source_sha256: str

    def __post_init__(self) -> None:
        for field_name in ("event_id", "order_id", "account_id", "strategy_id"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} is required")
        _utc("occurred_at", self.occurred_at)
        _sha256("source_sha256", self.source_sha256)
        if not isinstance(self.side, EventSide):
            raise ValueError("intent side must be an EventSide value")
        if type(self.quantity) is not int or self.quantity <= 0 or self.quantity % self.instrument.lot_size:
            raise ValueError("intent quantity must be a positive lot-size multiple")


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    order_id: str
    status: OrderStatus
    occurred_at: datetime
    source_sha256: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.order_id.strip():
            raise ValueError("order event and order IDs are required")
        _utc("occurred_at", self.occurred_at)
        _sha256("source_sha256", self.source_sha256)
        if not isinstance(self.status, OrderStatus):
            raise ValueError("order status must be an OrderStatus value")


@dataclass(frozen=True)
class FillEvent:
    event_id: str
    order_id: str
    instrument: InstrumentRef
    side: EventSide
    quantity: int
    price: Decimal
    occurred_at: datetime
    fidelity: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.order_id.strip():
            raise ValueError("fill event and order IDs are required")
        if type(self.quantity) is not int or self.quantity <= 0 or self.quantity % self.instrument.lot_size:
            raise ValueError("fill quantity must be a positive lot-size multiple")
        _positive("price", self.price)
        _utc("occurred_at", self.occurred_at)
        if not isinstance(self.side, EventSide):
            raise ValueError("fill side must be an EventSide value")
        if self.fidelity not in {"SIMULATED_BAR", "SIMULATED_QUOTE", "OBSERVED_BROKER"}:
            raise ValueError("fill fidelity is unknown")
        _sha256("source_sha256", self.source_sha256)


@dataclass(frozen=True)
class MarkEvent:
    event_id: str
    instrument: InstrumentRef
    price: Decimal
    occurred_at: datetime
    fidelity: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("mark event ID is required")
        _positive("price", self.price)
        _utc("occurred_at", self.occurred_at)
        if self.fidelity not in {"SIMULATED_BAR", "SIMULATED_QUOTE", "OBSERVED_BROKER"}:
            raise ValueError("mark fidelity is unknown")
        _sha256("source_sha256", self.source_sha256)


TradingEvent = IntentEvent | OrderEvent | FillEvent | MarkEvent


def canonical_event_payload(event: TradingEvent) -> bytes:
    """Serialize an immutable trading event deterministically for journals and cross-engine transport."""
    def normalize(value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    envelope = {"schema_version": "1", "event_type": type(event).__name__,
                "event": normalize(asdict(event))}
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def trading_event_sha256(event: TradingEvent) -> str:
    return hashlib.sha256(canonical_event_payload(event)).hexdigest()


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
