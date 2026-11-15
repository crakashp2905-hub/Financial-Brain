"""Instrument master with effective-dated rules and fail-closed tradability (FB-0041, FB-0083..0099, FB-0102).

An instrument is a canonical identity plus effective-dated market rules. Every rule lookup takes the decision date,
so historical replays see historical rules. Unknown instruments, suspended or expired contracts and sessions
outside the trading phase all make the instrument non-tradable; the caller must abstain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from enum import Enum

from financial_brain.kernel.units import to_decimal

ASSET_CLASSES = frozenset({"EQUITY", "INDEX", "FUTURE", "OPTION", "FX", "ETF"})


class Status(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"
    DELISTED = "DELISTED"


class Phase(str, Enum):
    CLOSED = "CLOSED"
    PREOPEN = "PREOPEN"
    CONTINUOUS = "CONTINUOUS"
    AUCTION = "AUCTION"
    SETTLEMENT = "SETTLEMENT"


class InstrumentError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentId:
    """Canonical identity: venue, asset class, symbol. Aliases are resolved elsewhere, never merged here."""

    venue: str
    asset_class: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.venue or not self.symbol:
            raise InstrumentError("venue and symbol are required")
        if self.asset_class not in ASSET_CLASSES:
            raise InstrumentError(f"unknown asset class {self.asset_class!r}")

    def key(self) -> str:
        return f"{self.venue}:{self.asset_class}:{self.symbol}"


@dataclass(frozen=True)
class RuleVersion:
    """Market rules effective from a date, with optional execution provenance and limits."""

    effective_from: date
    tick: Decimal
    lot: int = 1
    multiplier: Decimal = Decimal(1)
    settlement_lag_days: int = 1
    max_order_quantity: int | None = None
    exchange: str | None = None
    max_freeze_quantity: int | None = None
    margin_rule_id: str | None = None
    session_rule_id: str | None = None
    session_open: time | None = None
    session_close: time | None = None
    source_id: str | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tick", to_decimal(self.tick))
        object.__setattr__(self, "multiplier", to_decimal(self.multiplier))
        if self.tick <= 0 or self.lot < 1 or self.multiplier <= 0 or self.settlement_lag_days < 0:
            raise InstrumentError("tick, lot and multiplier must be positive; settlement lag non-negative")
        if self.max_order_quantity is not None and self.max_order_quantity < 1:
            raise InstrumentError("maximum order quantity must be positive")
        if self.max_freeze_quantity is not None and self.max_freeze_quantity < 1:
            raise InstrumentError("maximum freeze quantity must be positive")
        if self.session_open is not None and self.session_close is not None and self.session_open >= self.session_close:
            raise InstrumentError("session open must precede session close")
        if self.source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise InstrumentError("source SHA-256 must be 64 lowercase hexadecimal characters")


@dataclass
class Instrument:
    instrument: InstrumentId
    rules: list[RuleVersion]
    status_history: list[tuple[date, Status]] = field(default_factory=list)
    expiry: date | None = None

    def __post_init__(self) -> None:
        if not self.rules:
            raise InstrumentError("an instrument needs at least one rule version")
        self.rules = sorted(self.rules, key=lambda r: r.effective_from)
        if len({r.effective_from for r in self.rules}) != len(self.rules):
            raise InstrumentError("duplicate rule effective dates")
        if not self.status_history:
            self.status_history = [(self.rules[0].effective_from, Status.ACTIVE)]
        self.status_history.sort(key=lambda s: s[0])

    def rule_on(self, day: date) -> RuleVersion:
        applicable = [r for r in self.rules if r.effective_from <= day]
        if not applicable:
            raise InstrumentError(f"no rule effective on {day.isoformat()}; refusing to guess")
        return applicable[-1]

    def validate_order(self, *, day: date, exchange: str, quantity: int, price: Decimal,
                       local_time: time) -> RuleVersion:
        """Validate an executable order only when complete, source-identified rules exist for its date.

        Research callers may still use :meth:`rule_on` for historical tick conversion. This method is the stricter
        order gate and refuses incomplete owner-supplied Indian-market instrument, margin, session, or source rules.
        """
        tradable, reason = self.tradable(day, Phase.CONTINUOUS)
        if not tradable:
            raise InstrumentError(f"instrument is not tradable: {reason}")
        rule = self.rule_on(day)
        required = {
            "exchange": rule.exchange,
            "freeze quantity": rule.max_freeze_quantity,
            "margin rule": rule.margin_rule_id,
            "session rule": rule.session_rule_id,
            "session open": rule.session_open,
            "session close": rule.session_close,
            "source identifier": rule.source_id,
            "source SHA-256": rule.source_sha256,
        }
        missing = [name for name, value in required.items() if value is None or value == ""]
        if missing:
            raise InstrumentError("execution rules missing source-specific fields: " + ", ".join(missing))
        if rule.session_open is None or rule.session_close is None:
            raise InstrumentError("execution rules missing source-specific session window")
        if exchange != rule.exchange:
            raise InstrumentError("order exchange does not match the effective instrument rules")
        if type(quantity) is not int or quantity <= 0:
            raise InstrumentError("order quantity must be a positive integer")
        if quantity % rule.lot:
            raise InstrumentError(f"order quantity must be a multiple of lot size {rule.lot}")
        freeze = min(rule.max_freeze_quantity or 0, rule.max_order_quantity or rule.max_freeze_quantity or 0)
        if quantity > freeze:
            raise InstrumentError(f"order quantity exceeds effective freeze limit {freeze}")
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0 or price % rule.tick:
            raise InstrumentError(f"order price must be positive and aligned to tick {rule.tick}")
        if not rule.session_open <= local_time < rule.session_close:
            raise InstrumentError("order time is outside the effective exchange session")
        return rule

    def status_on(self, day: date) -> Status:
        current = None
        for d, s in self.status_history:
            if d <= day:
                current = s
        if current is None:
            return Status.DELISTED
        return current

    def tradable(self, day: date, phase: Phase) -> tuple[bool, str]:
        """Fail closed: returns (ok, reason). A non-trading phase or inactive status blocks orders."""
        if self.expiry is not None and day > self.expiry:
            return False, "EXPIRED_BY_DATE"
        status = self.status_on(day)
        if status is not Status.ACTIVE:
            return False, f"STATUS_{status.value}"
        if phase is not Phase.CONTINUOUS:
            return False, f"PHASE_{phase.value}"
        return True, "OK"


class InstrumentMaster:
    def __init__(self) -> None:
        self._items: dict[str, Instrument] = {}

    def add(self, item: Instrument) -> None:
        key = item.instrument.key()
        if key in self._items:
            raise InstrumentError(f"duplicate instrument {key}")
        self._items[key] = item

    def get(self, instrument: InstrumentId) -> Instrument:
        item = self._items.get(instrument.key())
        if item is None:
            raise InstrumentError(f"unknown instrument {instrument.key()}; no trade is possible")
        return item

    def __len__(self) -> int:
        return len(self._items)


def fx_pip_size(pair: str) -> Decimal:
    """Pip convention: JPY-quoted pairs use 0.01, others 0.0001 (FB-0095, FB-0334)."""
    if len(pair) != 6 or not pair.isalpha() or not pair.isupper():
        raise InstrumentError("FX pair must be six upper-case letters, e.g. EURUSD")
    return Decimal("0.01") if pair[3:] == "JPY" else Decimal("0.0001")


def fx_pip_value(pair: str, units: int, price: Decimal, quote_to_account: Decimal = Decimal(1)) -> Decimal:
    """Value of one pip in the account currency for a base-unit position."""
    pip = fx_pip_size(pair)
    return pip * to_decimal(units) * quote_to_account if price > 0 else Decimal(0)
