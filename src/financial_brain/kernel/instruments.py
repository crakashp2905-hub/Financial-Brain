"""Instrument master with effective-dated rules and fail-closed tradability (FB-0041, FB-0083..0099, FB-0102).

An instrument is a canonical identity plus effective-dated market rules. Every rule lookup takes the decision date,
so historical replays see historical rules. Unknown instruments, suspended or expired contracts and sessions
outside the trading phase all make the instrument non-tradable; the caller must abstain.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum

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
    LISTED = "LISTED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    HALTED = "HALTED"
    EXPIRED = "EXPIRED"
    DELISTED = "DELISTED"
    MATURED = "MATURED"
    CLOSED_FOR_PURCHASE = "CLOSED_FOR_PURCHASE"
    CLOSED_FOR_REDEMPTION = "CLOSED_FOR_REDEMPTION"
    RESTRICTED = "RESTRICTED"


class Phase(str, Enum):
    CLOSED = "CLOSED"
    PREOPEN = "PREOPEN"
    CONTINUOUS = "CONTINUOUS"
    AUCTION = "AUCTION"
    SETTLEMENT = "SETTLEMENT"


class InstrumentError(ValueError):
    pass


@dataclass(frozen=True)
class NFOContractRecord:
    """One provider-listed NFO option identity as observed in an effective-dated master."""

    exchange: str
    underlying: str
    right: str
    strike: Decimal
    expiry: date
    instrument_token: int
    tradingsymbol: str
    effective_from: date
    effective_to: date | None = None
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        from decimal import InvalidOperation

        values = (self.exchange, self.underlying, self.right, self.tradingsymbol)
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise InstrumentError("NFO contract exchange, underlying, right and listed symbol are required")
        exchange = self.exchange.strip().upper()
        right = self.right.strip().upper()
        symbol = self.tradingsymbol.strip().upper()
        underlying = self.underlying.strip().upper()
        if exchange != "NFO":
            raise InstrumentError("option contract exchange must be NFO")
        if right not in {"CE", "PE"}:
            raise InstrumentError("option right must be CE or PE")
        try:
            strike = to_decimal(self.strike)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise InstrumentError("option strike must be a finite positive decimal") from exc
        if not strike.is_finite() or strike <= 0:
            raise InstrumentError("option strike must be a finite positive decimal")
        if type(self.instrument_token) is not int or self.instrument_token <= 0:
            raise InstrumentError("effective provider instrument token must be a positive integer")
        if self.expiry < self.effective_from:
            raise InstrumentError("option expiry precedes the contract effective date")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise InstrumentError("contract effective_to precedes effective_from")
        status = self.status.strip().upper()
        if status not in {"ACTIVE", "DELISTED", "EXPIRED", "SUSPENDED"}:
            raise InstrumentError("unsupported listed-contract status")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "strike", strike)
        object.__setattr__(self, "tradingsymbol", symbol)
        object.__setattr__(self, "status", status)

    def canonical_id(self) -> str:
        """Return canonical JSON including exchange, terms and the effective provider token."""
        return json.dumps((self.exchange, self.underlying, self.right, format(self.strike, "f"),
                           self.expiry.isoformat(), self.instrument_token,
                           self.effective_from.isoformat()),
                          ensure_ascii=True, separators=(",", ":"))

    def eligible_on(self, day: date) -> bool:
        return (self.status == "ACTIVE" and self.effective_from <= day <= self.expiry
                and (self.effective_to is None or day <= self.effective_to))


class NFOContractMaster:
    """Resolve only exact, uniquely listed provider contracts; never construct a guessed symbol."""

    def __init__(self, records: tuple[NFOContractRecord, ...] = ()) -> None:
        self._records: dict[str, NFOContractRecord] = {}
        self._symbols: dict[str, str] = {}
        self._tokens: dict[int, str] = {}
        for record in records:
            self.add(record)

    def add(self, record: NFOContractRecord) -> None:
        key = record.canonical_id()
        symbol_key = (record.exchange, record.tradingsymbol)
        prior_key = self._symbols.get("|".join(symbol_key))
        token_key = self._tokens.get(record.instrument_token)
        if key in self._records:
            raise InstrumentError("duplicate canonical NFO contract")
        if prior_key is not None or token_key is not None:
            raise InstrumentError("conflicting NFO symbol or effective provider token")
        for prior in self._records.values():
            same_terms = (prior.exchange, prior.underlying, prior.right, prior.strike, prior.expiry) == (
                record.exchange, record.underlying, record.right, record.strike, record.expiry)
            if same_terms and max(prior.effective_from, record.effective_from) <= min(
                    prior.effective_to or date.max, record.effective_to or date.max):
                raise InstrumentError("overlapping effective NFO contract identities")
        self._records[key] = record
        self._symbols["|".join(symbol_key)] = key
        self._tokens[record.instrument_token] = key

    def resolve(self, *, exchange: str, underlying: str, right: str, strike: Decimal,
                expiry: date, instrument_token: int, on: date) -> NFOContractRecord:
        requested = (exchange.strip().upper(), underlying.strip().upper(), right.strip().upper(),
                     to_decimal(strike), expiry, instrument_token)
        matches = [record for record in self._records.values()
                   if (record.exchange, record.underlying, record.right, record.strike,
                       record.expiry, record.instrument_token) == requested
                   and record.eligible_on(on)]
        if len(matches) != 1:
            raise InstrumentError("unknown, stale, delisted, expired or ambiguous NFO contract; no trade is possible")
        return matches[0]


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
    underlying_index: str | None = None
    contract_expiry: date | None = None
    scheme_code: str | None = None
    plan: str | None = None
    option: str | None = None

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
        for name in ("underlying_index", "scheme_code", "plan", "option"):
            value = getattr(self, name)
            if value is not None:
                normalized = value.strip().upper()
                if not normalized:
                    raise InstrumentError(f"{name} cannot be blank")
                object.__setattr__(self, name, normalized)
        if asset_class == AssetClass.MUTUAL_FUND.value and not all(
                (self.scheme_code, self.plan, self.option)):
            raise InstrumentError("mutual-fund identity requires scheme_code, plan and option")
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
                 ",".join(self.underlying_lineage), self.underlying_index or "",
                 self.contract_expiry.isoformat() if self.contract_expiry else "",
                 self.scheme_code or "", self.plan or "", self.option or "")
        return json.dumps(parts, ensure_ascii=True, separators=(",", ":"))

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
    effective_to: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tick", to_decimal(self.tick))
        object.__setattr__(self, "multiplier", to_decimal(self.multiplier))
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise InstrumentError("rule effective_to precedes effective_from")
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
        identity_expiry = self.instrument.contract_expiry
        if identity_expiry and self.expiry and identity_expiry != self.expiry:
            raise InstrumentError("instrument expiry conflicts with canonical InstrumentId")
        if identity_expiry and self.expiry is None:
            self.expiry = identity_expiry
        self.rules = sorted(self.rules, key=lambda r: r.effective_from)
        if len({r.effective_from for r in self.rules}) != len(self.rules):
            raise InstrumentError("duplicate rule effective dates")
        for prior, following in zip(self.rules, self.rules[1:]):
            if prior.effective_to is not None and prior.effective_to >= following.effective_from:
                raise InstrumentError("overlapping effective market-rule versions")
        if not self.status_history:
            self.status_history = [(self.rules[0].effective_from, Status.ACTIVE)]
        self.status_history.sort(key=lambda s: s[0])
        if len({day for day, _ in self.status_history}) != len(self.status_history):
            raise InstrumentError("duplicate instrument status effective dates")
        self.status_history = [(day, Status(status)) for day, status in self.status_history]
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
        applicable = [r for r in self.rules if r.effective_from <= day
                      and (r.effective_to is None or day <= r.effective_to)]
        if not applicable:
            raise InstrumentError(f"no rule effective on {day.isoformat()}; refusing to guess")
        return max(applicable, key=lambda rule: rule.effective_from)

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
        if status not in {Status.ACTIVE, Status.LISTED}:
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
