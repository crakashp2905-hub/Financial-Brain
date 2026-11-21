"""Instrument master with effective-dated rules and fail-closed tradability (FB-0041, FB-0083..0099, FB-0102).

An instrument is a canonical identity plus effective-dated market rules. Every rule lookup takes the decision date,
so historical replays see historical rules. Unknown instruments, suspended or expired contracts and sessions
outside the trading phase all make the instrument non-tradable; the caller must abstain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum
from urllib.parse import quote

from financial_brain.kernel.units import to_decimal

ASSET_CLASSES = frozenset({"EQUITY", "INDEX", "FUTURE", "OPTION", "FX", "ETF"})


class AssetClass(StrEnum):
    """Product-level asset class; deliberately independent from its trading desk."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    MUTUAL_FUND = "MUTUAL_FUND"


class Desk(StrEnum):
    """Operating workflow selected for a decision, not an instrument classification."""

    SWING = "SWING"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"


_DESK_ASSET_CLASSES: dict[Desk, frozenset[AssetClass]] = {
    Desk.SWING: frozenset({AssetClass.EQUITY, AssetClass.ETF}),
    Desk.INTRADAY: frozenset({AssetClass.EQUITY, AssetClass.ETF}),
    Desk.OPTIONS: frozenset({AssetClass.OPTION}),
    Desk.ETF: frozenset({AssetClass.ETF}),
    Desk.MUTUAL_FUND: frozenset({AssetClass.MUTUAL_FUND}),
}


def desk_supports_asset_class(desk: Desk, asset_class: AssetClass) -> bool:
    """Return whether a desk can handle an asset class without conflating their identities."""
    return AssetClass(asset_class) in _DESK_ASSET_CLASSES[Desk(desk)]


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
    """Canonical point-in-time identity; ``venue`` remains the legacy exchange argument."""

    venue: str
    asset_class: str
    symbol: str
    market: str = "IN"
    exchange: str | None = None
    isin: str | None = None
    provider_token: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    underlying_lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        market = self.market.strip().upper()
        exchange = (self.exchange or self.venue).strip().upper()
        venue = self.venue.strip().upper()
        symbol = self.symbol.strip().upper()
        asset_class = self.asset_class.strip().upper()
        if not market or not exchange or not symbol:
            raise InstrumentError("market, exchange/venue and symbol are required")
        if venue and venue != exchange:
            raise InstrumentError("legacy venue and canonical exchange must agree")
        if asset_class not in ASSET_CLASSES | {AssetClass.MUTUAL_FUND.value}:
            raise InstrumentError(f"unknown asset class {self.asset_class!r}")
        if self.isin is not None and (len(self.isin) != 12 or not self.isin.isalnum()):
            raise InstrumentError("ISIN must be 12 alphanumeric characters")
        if self.provider_token is not None and not self.provider_token.strip():
            raise InstrumentError("provider_token cannot be blank")
        if (self.effective_from is not None and self.effective_to is not None
                and self.effective_to < self.effective_from):
            raise InstrumentError("effective_to precedes effective_from")
        lineage = tuple(value.strip().upper() for value in self.underlying_lineage)
        if any(not value for value in lineage) or len(lineage) != len(set(lineage)):
            raise InstrumentError("underlying lineage must contain unique, non-empty identities")
        if symbol in lineage:
            raise InstrumentError("instrument cannot be its own underlying")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "venue", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "underlying_lineage", lineage)

    def key(self) -> str:
        """Stable full identity key, including provider and effective-date distinctions."""
        parts = (self.market, self.exchange or self.venue, self.asset_class, self.symbol,
                 self.isin or "", self.provider_token or "",
                 self.effective_from.isoformat() if self.effective_from else "",
                 self.effective_to.isoformat() if self.effective_to else "",
                 ",".join(self.underlying_lineage))
        return "|".join(quote(value, safe="") for value in parts)

    def same_listing_series(self, other: InstrumentId) -> bool:
        """Whether two versions refer to the same market/exchange/symbol history."""
        return (self.market, self.exchange, self.symbol) == (other.market, other.exchange, other.symbol)


@dataclass(frozen=True)
class RuleVersion:
    """Market rules effective from a date (tick, lot, multiplier, settlement lag in days)."""

    effective_from: date
    tick: Decimal
    lot: int = 1
    multiplier: Decimal = Decimal(1)
    settlement_lag_days: int = 1
    max_order_quantity: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tick", to_decimal(self.tick))
        object.__setattr__(self, "multiplier", to_decimal(self.multiplier))
        if self.tick <= 0 or self.lot < 1 or self.multiplier <= 0 or self.settlement_lag_days < 0:
            raise InstrumentError("tick, lot and multiplier must be positive; settlement lag non-negative")


@dataclass
class Instrument:
    instrument: InstrumentId
    rules: list[RuleVersion]
    status_history: list[tuple[date, Status]] = field(default_factory=list)
    expiry: date | None = None
    isin: str | None = None
    strike: Decimal | None = None
    rights_status: str = "unverified"

    def __post_init__(self) -> None:
        if not self.rules:
            raise InstrumentError("an instrument needs at least one rule version")
        identity_isin = self.instrument.isin
        if identity_isin and self.isin and identity_isin != self.isin:
            raise InstrumentError("Instrument ISIN conflicts with canonical InstrumentId")
        if identity_isin and self.isin is None:
            self.isin = identity_isin
        self.rules = sorted(self.rules, key=lambda r: r.effective_from)
        if len({r.effective_from for r in self.rules}) != len(self.rules):
            raise InstrumentError("duplicate rule effective dates")
        if not self.status_history:
            self.status_history = [(self.rules[0].effective_from, Status.ACTIVE)]
        self.status_history.sort(key=lambda s: s[0])
        if self.strike is not None:
            self.strike = to_decimal(self.strike)
            if self.strike <= 0:
                raise InstrumentError("strike must be positive")
        if self.rights_status not in {"unverified", "authorized", "restricted"}:
            raise InstrumentError("rights_status must be unverified, authorized or restricted")
        if self.isin is not None and (len(self.isin) != 12 or not self.isin.isalnum()):
            raise InstrumentError("ISIN must be 12 alphanumeric characters")

    def eligible(self, day: date, phase: Phase, *, quantity: int, price: Decimal,
                 require_rights: bool = True) -> tuple[bool, tuple[str, ...]]:
        """Return ticket eligibility with all fail-closed reasons for a dated instrument."""
        from financial_brain.kernel.market_rules import TicketRules, validate_ticket

        reasons: list[str] = []
        tradable, reason = self.tradable(day, phase)
        if not tradable:
            reasons.append(reason)
        try:
            rule = self.rule_on(day)
        except InstrumentError:
            reasons.append("MISSING_EFFECTIVE_RULE")
        else:
            if rule.max_order_quantity is None:
                reasons.append("MISSING_FREEZE_LIMIT")
            else:
                reasons.extend(validate_ticket(quantity, price, TicketRules(
                    lot_size=rule.lot, min_notional=Decimal(0), max_freeze_quantity=rule.max_order_quantity)))
        if self.instrument.asset_class in {"EQUITY", "ETF"} and not self.isin:
            reasons.append("MISSING_ISIN")
        if self.instrument.asset_class in {"OPTION", "FUTURE"} and self.expiry is None:
            reasons.append("MISSING_EXPIRY")
        if self.instrument.asset_class == "OPTION" and self.strike is None:
            reasons.append("MISSING_STRIKE")
        if require_rights and self.rights_status != "authorized":
            reasons.append("INSTRUMENT_RIGHTS_UNVERIFIED")
        return not reasons, tuple(reasons)

    def rule_on(self, day: date) -> RuleVersion:
        applicable = [r for r in self.rules if r.effective_from <= day]
        if not applicable:
            raise InstrumentError(f"no rule effective on {day.isoformat()}; refusing to guess")
        return applicable[-1]

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
        if self.instrument.effective_from is not None and day < self.instrument.effective_from:
            return False, "NOT_YET_EFFECTIVE"
        if self.instrument.effective_to is not None and day > self.instrument.effective_to:
            return False, "NO_LONGER_EFFECTIVE"
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
        for prior in self._items.values():
            left = prior.instrument
            right = item.instrument
            if not left.same_listing_series(right):
                continue
            left_start = left.effective_from or date.min
            right_start = right.effective_from or date.min
            left_end = left.effective_to or date.max
            right_end = right.effective_to or date.max
            if max(left_start, right_start) <= min(left_end, right_end):
                raise InstrumentError(
                    f"overlapping effective identities for {right.market}:{right.exchange}:{right.symbol}"
                )
        self._items[key] = item

    def get(self, instrument: InstrumentId) -> Instrument:
        item = self._items.get(instrument.key())
        if item is None:
            raise InstrumentError(f"unknown instrument {instrument.key()}; no trade is possible")
        return item

    def resolve(self, *, market: str, exchange: str, symbol: str, on: date) -> Instrument:
        """Resolve one listing version as known for a historical date; ambiguity fails closed."""
        market_key, exchange_key, symbol_key = market.upper(), exchange.upper(), symbol.upper()
        matches = [item for item in self._items.values()
                   if (item.instrument.market, item.instrument.exchange, item.instrument.symbol)
                   == (market_key, exchange_key, symbol_key)
                   and (item.instrument.effective_from is None or item.instrument.effective_from <= on)
                   and (item.instrument.effective_to is None or on <= item.instrument.effective_to)]
        if len(matches) != 1:
            state = "unknown" if not matches else "ambiguous"
            raise InstrumentError(f"{state} instrument identity {market_key}:{exchange_key}:{symbol_key} on {on}")
        return matches[0]

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
