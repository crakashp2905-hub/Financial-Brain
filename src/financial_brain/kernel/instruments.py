"""Instrument master with effective-dated rules and fail-closed tradability (FB-0041, FB-0083..0099, FB-0102).

An instrument is a canonical identity plus effective-dated market rules. Every rule lookup takes the decision date,
so historical replays see historical rules. Unknown instruments, suspended or expired contracts and sessions
outside the trading phase all make the instrument non-tradable; the caller must abstain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from financial_brain.kernel.clock import ClockError, MarketFactTimes, utc
from financial_brain.kernel.units import to_decimal


class AssetClass(str, Enum):
    """Instrument kind, kept independent from the strategy desk using it."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    MUTUAL_FUND = "MUTUAL_FUND"


class Desk(str, Enum):
    """Decision workflow. ETFs, for example, can be traded by either equity desk."""

    SWING = "SWING"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"


ASSET_CLASSES = frozenset({*(item.value for item in AssetClass), "INDEX", "FUTURE", "FX"})

_DESK_ASSET_CLASSES: dict[Desk, frozenset[AssetClass]] = {
    Desk.SWING: frozenset({AssetClass.EQUITY, AssetClass.ETF}),
    Desk.INTRADAY: frozenset({AssetClass.EQUITY, AssetClass.ETF}),
    Desk.OPTIONS: frozenset({AssetClass.OPTION}),
    Desk.ETF: frozenset({AssetClass.ETF}),
    Desk.MUTUAL_FUND: frozenset({AssetClass.MUTUAL_FUND}),
}


def desk_supports_asset_class(desk: Desk, asset_class: AssetClass) -> bool:
    """Return whether a decision desk supports an instrument asset class."""
    return AssetClass(asset_class) in _DESK_ASSET_CLASSES[Desk(desk)]


class Status(str, Enum):
    LISTED = "LISTED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    HALTED = "HALTED"
    EXPIRED = "EXPIRED"
    MATURED = "MATURED"
    DELISTED = "DELISTED"
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
class InstrumentId:
    """Canonical identity with optional stable lineage and dated provider identity."""

    venue: str
    asset_class: str
    symbol: str
    market: str | None = "IN"
    exchange: str | None = None
    isin: str | None = None
    provider_token: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    underlying_lineage: str | tuple[str, ...] | None = None
    underlying_index: str | None = None
    contract_expiry: date | None = None
    scheme_code: str | None = None
    plan: str | None = None
    option: str | None = None

    def __post_init__(self) -> None:
        venue = self.venue.strip().upper()
        exchange = (self.exchange or venue).strip().upper()
        market = (self.market or "IN").strip().upper()
        symbol = self.symbol.strip().upper()
        asset_class = self.asset_class.value if isinstance(self.asset_class, AssetClass) else self.asset_class.strip().upper()
        if not venue or not exchange or not symbol or not market:
            raise InstrumentError("market, exchange/venue and symbol are required")
        if venue != exchange:
            raise InstrumentError("legacy venue and canonical exchange must agree")
        if asset_class not in ASSET_CLASSES:
            raise InstrumentError(f"unknown asset class {self.asset_class!r}")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "asset_class", asset_class)
        for name in ("provider_token", "underlying_index", "scheme_code", "plan", "option"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise InstrumentError(f"{name} cannot be blank")
            if value is not None:
                object.__setattr__(self, name, value.strip().upper())
        lineage = self.underlying_lineage
        if isinstance(lineage, str):
            if not lineage.strip():
                raise InstrumentError("underlying_lineage cannot be blank")
            object.__setattr__(self, "underlying_lineage", lineage.strip())
        elif lineage is not None:
            normalized = tuple(value.strip() for value in lineage)
            if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
                raise InstrumentError("underlying lineage must contain unique, non-empty identities")
            object.__setattr__(self, "underlying_lineage", normalized)
        if self.isin is not None and (len(self.isin) != 12 or not self.isin.isalnum()):
            raise InstrumentError("ISIN must be 12 alphanumeric characters")
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise InstrumentError("identity effective_to must be later than effective_from")
        if self.asset_class == AssetClass.MUTUAL_FUND.value and not all((self.scheme_code, self.plan, self.option)):
            raise InstrumentError("scheme_code, plan and option are required together")

    def key(self) -> str:
        if self.underlying_lineage:
            lineage = ":".join(self.underlying_lineage) if isinstance(self.underlying_lineage, tuple) else self.underlying_lineage
            return f"LINEAGE:{self.asset_class}:{lineage}"
        version = self.isin or (self.effective_from.isoformat() if self.effective_from else None)
        suffix = f":{version}" if version else ""
        return f"{self.venue}:{self.asset_class}:{self.symbol}{suffix}"


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
        if self.tick <= 0 or self.lot < 1 or self.multiplier <= 0 or self.settlement_lag_days < 0:
            raise InstrumentError("tick, lot and multiplier must be positive; settlement lag non-negative")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise InstrumentError("rule effective_to must be later than effective_from")


@dataclass(frozen=True)
class InstrumentMetadataVersion:
    """Effective-dated contract, ETF and fund metadata; intervals are start-inclusive/end-exclusive."""

    effective_from: date
    effective_to: date | None = None
    isin: str | None = None
    expiry: date | None = None
    strike: Decimal | None = None
    underlying_index: str | None = None
    scheme_id: str | None = None
    plan: str | None = None

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise InstrumentError("metadata effective_to must be later than effective_from")
        if self.isin is not None and (len(self.isin) != 12 or not self.isin.isalnum()):
            raise InstrumentError("ISIN must be 12 alphanumeric characters")
        if self.strike is not None:
            object.__setattr__(self, "strike", to_decimal(self.strike))
            if self.strike <= 0:
                raise InstrumentError("strike must be positive")
        for name in ("underlying_index", "scheme_id", "plan"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise InstrumentError(f"{name} cannot be blank")


@dataclass(frozen=True)
class InstrumentIdentityVersion:
    """Dated external alias with start-inclusive/end-exclusive resolution dates."""

    effective_from: date
    market: str
    exchange: str
    symbol: str
    isin: str | None = None
    provider_token: str | None = None
    effective_to: date | None = None
    underlying_lineage: str | None = None
    successor_lineage: str | None = None

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise InstrumentError("identity effective_to must be later than effective_from")
        for name in ("market", "exchange", "symbol"):
            if not getattr(self, name).strip():
                raise InstrumentError(f"identity {name} is required")
        if self.isin is not None and (len(self.isin) != 12 or not self.isin.isalnum()):
            raise InstrumentError("ISIN must be 12 alphanumeric characters")
        for name in ("provider_token", "underlying_lineage", "successor_lineage"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise InstrumentError(f"identity {name} cannot be blank")


@dataclass(frozen=True)
class InstrumentObservation:
    """Source-bound market fact that must resolve to one dated instrument identity."""

    market: str
    exchange: str
    asset_class: str
    symbol: str
    as_of: date
    times: MarketFactTimes
    source_id: str
    record_sha256: str

    def __post_init__(self) -> None:
        for name in ("market", "exchange", "asset_class", "symbol", "source_id"):
            if not getattr(self, name).strip():
                raise InstrumentError(f"observation {name} is required")
        if self.asset_class not in ASSET_CLASSES:
            raise InstrumentError(f"unknown asset class {self.asset_class!r}")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.record_sha256):
            raise InstrumentError("observation record needs a SHA-256 digest")


@dataclass
class Instrument:
    instrument: InstrumentId
    rules: list[RuleVersion]
    status_history: list[tuple[date, Status]] = field(default_factory=list)
    expiry: date | None = None
    isin: str | None = None
    strike: Decimal | None = None
    rights_status: str = "unverified"
    metadata_versions: list[InstrumentMetadataVersion] = field(default_factory=list)
    identity_versions: list[InstrumentIdentityVersion] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.expiry is None:
            self.expiry = self.instrument.contract_expiry
        if self.isin is None:
            self.isin = self.instrument.isin
        if self.strike is not None:
            self.strike = to_decimal(self.strike)
        if not self.rules:
            raise InstrumentError("an instrument needs at least one rule version")
        self.rules = sorted(self.rules, key=lambda r: r.effective_from)
        if len({r.effective_from for r in self.rules}) != len(self.rules):
            raise InstrumentError("duplicate rule effective dates")
        for previous, current in zip(self.rules, self.rules[1:]):
            if previous.effective_to is not None and current.effective_from < previous.effective_to:
                raise InstrumentError("overlapping rule effective intervals")
        self.metadata_versions = sorted(self.metadata_versions, key=lambda item: item.effective_from)
        if len({item.effective_from for item in self.metadata_versions}) != len(self.metadata_versions):
            raise InstrumentError("duplicate metadata effective dates")
        for previous, current in zip(self.metadata_versions, self.metadata_versions[1:]):
            if previous.effective_to is not None and current.effective_from < previous.effective_to:
                raise InstrumentError("overlapping metadata effective intervals")
        self.identity_versions = sorted(self.identity_versions, key=lambda item: item.effective_from)
        for index, current in enumerate(self.identity_versions):
            for other in self.identity_versions[index + 1 :]:
                same_alias = (current.market, current.exchange, current.symbol) == (
                    other.market,
                    other.exchange,
                    other.symbol,
                )
                if not same_alias:
                    continue
                current_end = current.effective_to or date.max
                other_end = other.effective_to or date.max
                if current.effective_from < other_end and other.effective_from < current_end:
                    raise InstrumentError("overlapping active instrument identities")
        if not self.status_history:
            self.status_history = [(self.rules[0].effective_from, Status.ACTIVE)]
        self.status_history.sort(key=lambda s: s[0])
        if len({day for day, _ in self.status_history}) != len(self.status_history):
            raise InstrumentError("duplicate instrument status effective date")
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
        for index, rule in enumerate(self.rules):
            effective_to = rule.effective_to
            if effective_to is None and index + 1 < len(self.rules):
                effective_to = self.rules[index + 1].effective_from
            if rule.effective_from <= day and (effective_to is None or day < effective_to):
                return rule
        raise InstrumentError(f"no rule effective on {day.isoformat()}; refusing to guess")

    def metadata_on(self, day: date) -> InstrumentMetadataVersion:
        for index, version in enumerate(self.metadata_versions):
            effective_to = version.effective_to
            if effective_to is None and index + 1 < len(self.metadata_versions):
                effective_to = self.metadata_versions[index + 1].effective_from
            if version.effective_from <= day and (effective_to is None or day < effective_to):
                return version
        raise InstrumentError(f"no metadata effective on {day.isoformat()}; refusing to guess")

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
            return False, "NO_LONGER_EFFECTIVE"
        if self.instrument.effective_to is not None and day >= self.instrument.effective_to:
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
        self._aliases: dict[tuple[str, str, str, str], list[tuple[date, date | None, Instrument]]] = {}

    def add(self, item: Instrument) -> None:
        key = item.instrument.key()
        if key in self._items:
            raise InstrumentError(f"duplicate instrument {key}")
        start = item.instrument.effective_from or item.rules[0].effective_from
        base_identity = InstrumentIdentityVersion(
            effective_from=start,
            effective_to=item.instrument.effective_to,
            market=item.instrument.market or "IN",
            exchange=item.instrument.exchange or item.instrument.venue,
            symbol=item.instrument.symbol,
            isin=item.instrument.isin,
            provider_token=item.instrument.provider_token,
            underlying_lineage=(item.instrument.underlying_lineage
                                if isinstance(item.instrument.underlying_lineage, str) else None),
        )
        identities = item.identity_versions or [base_identity]
        for identity in identities:
            alias_key = (identity.market, identity.exchange, item.instrument.asset_class, identity.symbol)
            for start, end, _existing in self._aliases.get(alias_key, []):
                new_end = identity.effective_to or date.max
                old_end = end or date.max
                # Treat a shared boundary as a collision during registration. This is
                # deliberately stricter than point-in-time resolution, whose intervals
                # are half-open, because the master must not admit duplicate listings
                # while one source ends and another starts on the same market date.
                if identity.effective_from <= old_end and start <= new_end:
                    raise InstrumentError(
                        f"overlapping active identity alias (overlapping effective identities) "
                        f"{identity.market}:{identity.exchange}:{identity.symbol}"
                    )
        self._items[key] = item
        for identity in identities:
            alias_key = (identity.market, identity.exchange, item.instrument.asset_class, identity.symbol)
            self._aliases.setdefault(alias_key, []).append(
                (identity.effective_from, identity.effective_to, item)
            )

    def get(self, instrument: InstrumentId) -> Instrument:
        item = self._items.get(instrument.key())
        if item is None:
            raise InstrumentError(f"unknown instrument {instrument.key()}; no trade is possible")
        return item

    def resolve(
        self, market: str, exchange: str, asset_class: str | None = None, symbol: str = "",
        as_of: date | None = None, *, on: date | None = None,
    ) -> Instrument:
        """Resolve a point-in-time alias to its stable canonical instrument; ambiguity refuses."""
        if as_of is not None and on is not None and as_of != on:
            raise InstrumentError("as_of and on dates disagree")
        as_of = as_of or on
        if as_of is None:
            raise InstrumentError("a point-in-time resolution date is required")
        market, exchange, symbol = market.strip().upper(), exchange.strip().upper(), symbol.strip().upper()
        normalized_class = asset_class.strip().upper() if asset_class is not None else None
        alias_key = (market, exchange, normalized_class or "*", symbol)
        matches = [
            instrument
            for (alias_market, alias_exchange, alias_class, alias_symbol), aliases in self._aliases.items()
            if (alias_market, alias_exchange, alias_symbol) == (market, exchange, symbol)
            and (normalized_class is None or alias_class == normalized_class)
            for start, end, instrument in aliases
            if start <= as_of and (end is None or as_of < end)
        ]
        if not matches:
            raise InstrumentError(f"unknown instrument identity {alias_key} on {as_of}; no trade is possible")
        if len(matches) != 1:
            raise InstrumentError(f"ambiguous instrument identity {alias_key} on {as_of}")
        return matches[0]

    def resolve_observation(
        self, observation: InstrumentObservation, decision_time: datetime
    ) -> Instrument:
        """Resolve identity and timestamp provenance together, refusing look-ahead facts."""
        decision = utc(decision_time)
        try:
            observation.times.assert_usable_at(decision)
        except ClockError as exc:
            raise InstrumentError(f"unusable observation: {exc}") from exc
        for value in (observation.times.source_at, observation.times.exchange_at):
            if value is not None and value > decision:
                raise InstrumentError("observation carries a future source or exchange timestamp")
        return self.resolve(
            observation.market,
            observation.exchange,
            observation.asset_class,
            observation.symbol,
            observation.as_of,
        )

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
