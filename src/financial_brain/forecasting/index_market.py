"""Point-in-time index catalogue and complete 15-minute index bars.

This is a pure data-contract layer. It does not fetch Kite/NSE data or infer optionability;
callers must supply effective-dated records from an authorized instrument snapshot.
Index packets have no required equity volume/depth fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BAR = timedelta(minutes=15)
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class IndexMarketError(ValueError):
    """Invalid point-in-time index metadata, bars, or labels."""


@dataclass(frozen=True)
class IndexRecord:
    canonical_id: str
    name: str
    provider_symbol: str
    provider_token: int
    exchange: str
    family: str
    publication_clock: str
    valid_from: date
    valid_to: date | None = None
    history_from: date | None = None
    history_to: date | None = None

    def __post_init__(self) -> None:
        if not all((self.canonical_id, self.name, self.provider_symbol, self.exchange,
                    self.family, self.publication_clock)) or self.provider_token <= 0:
            raise IndexMarketError("index identity, clock and positive provider token are required")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise IndexMarketError("index effective interval is inverted")
        if self.history_from and self.history_to and self.history_to < self.history_from:
            raise IndexMarketError("index history interval is inverted")


@dataclass(frozen=True)
class OptionableIndexRecord:
    canonical_id: str
    underlying: str
    exchange: str
    source_snapshot_sha256: str
    valid_from: date
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not all((self.canonical_id, self.underlying, self.exchange)):
            raise IndexMarketError("optionable index identity is required")
        if len(self.source_snapshot_sha256) != 64 or any(
                c not in "0123456789abcdef" for c in self.source_snapshot_sha256):
            raise IndexMarketError("optionability needs a lowercase SHA-256 source snapshot")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise IndexMarketError("optionability effective interval is inverted")


@dataclass(frozen=True)
class ListedOptionContract:
    """One observed listed option from a source snapshot; never synthesized from an index."""

    canonical_id: str
    underlying: str
    exchange: str
    tradingsymbol: str
    instrument_token: int
    expiry: date
    strike: float
    right: str
    lot_size: int
    tick_size: float
    source_snapshot_sha256: str

    def __post_init__(self) -> None:
        if not all((self.canonical_id, self.underlying, self.exchange, self.tradingsymbol)):
            raise IndexMarketError("listed contract requires exact source identifiers")
        if self.instrument_token <= 0 or self.lot_size <= 0:
            raise IndexMarketError("listed contract token and lot size must be positive")
        if self.right not in {"CE", "PE"}:
            raise IndexMarketError("listed contract right must be CE or PE")
        if not isfinite(self.strike) or self.strike <= 0 or not isfinite(self.tick_size) or self.tick_size <= 0:
            raise IndexMarketError("listed contract strike and tick must be finite and positive")
        if len(self.source_snapshot_sha256) != 64 or any(
                c not in "0123456789abcdef" for c in self.source_snapshot_sha256):
            raise IndexMarketError("listed contract needs a lowercase SHA-256 source snapshot")


@dataclass(frozen=True)
class OptionResolution:
    status: str
    contracts: tuple[ListedOptionContract, ...]
    reason: str


class IndexCatalogue:
    """Separate index forecast coverage from effective-dated option eligibility."""

    def __init__(self, indices: list[IndexRecord], optionable: list[OptionableIndexRecord],
                 listed_contracts: list[ListedOptionContract] | None = None):
        self.indices = tuple(indices)
        self.optionable = tuple(optionable)
        self.listed_contracts = tuple(listed_contracts or ())
        for records, key in ((self.indices, lambda r: (r.canonical_id, r.valid_from)),
                             (self.optionable, lambda r: (r.canonical_id, r.valid_from))):
            if len({key(row) for row in records}) != len(records):
                raise IndexMarketError("duplicate effective-dated catalogue record")
        for record in self.optionable:
            if not any(i.canonical_id == record.canonical_id for i in self.indices):
                raise IndexMarketError("optionable index must exist in forecast index catalogue")
        optionable_keys = {(r.canonical_id, r.underlying.upper(), r.exchange.upper())
                           for r in self.optionable}
        for contract in self.listed_contracts:
            option_key = (contract.canonical_id, contract.underlying.upper(), contract.exchange.upper())
            if option_key not in optionable_keys:
                raise IndexMarketError("listed contract does not match an effective optionability record")
        contract_keys = [(r.canonical_id, r.tradingsymbol, r.expiry, r.source_snapshot_sha256)
                         for r in self.listed_contracts]
        if len(set(contract_keys)) != len(contract_keys):
            raise IndexMarketError("duplicate listed option contract in source snapshot")
        self._validate_no_overlap(self.indices)
        self._validate_no_overlap(self.optionable)

    @staticmethod
    def _validate_no_overlap(records) -> None:
        by_id: dict[str, list] = {}
        for row in records:
            by_id.setdefault(row.canonical_id, []).append(row)
        for rows in by_id.values():
            rows.sort(key=lambda r: r.valid_from)
            for left, right in zip(rows, rows[1:]):
                if left.valid_to is None or left.valid_to >= right.valid_from:
                    raise IndexMarketError("effective-dated catalogue intervals overlap")

    def index_at(self, canonical_id: str, day: date) -> IndexRecord | None:
        return next((r for r in self.indices if r.canonical_id == canonical_id
                     and r.valid_from <= day and (r.valid_to is None or day <= r.valid_to)), None)

    def is_optionable_at(self, canonical_id: str, day: date) -> bool:
        return any(r.canonical_id == canonical_id and r.valid_from <= day
                   and (r.valid_to is None or day <= r.valid_to) for r in self.optionable)

    def resolve_options(self, canonical_id: str, day: date) -> OptionResolution:
        """Resolve only listed, unexpired contracts; absent/unsupported rows mean NO_OPTION_TRADE."""
        index = self.index_at(canonical_id, day)
        if index is None:
            return OptionResolution("NO_OPTION_TRADE", (), "index_not_effective")
        optionability = next((r for r in self.optionable if r.canonical_id == canonical_id
                              and r.valid_from <= day and (r.valid_to is None or day <= r.valid_to)), None)
        if optionability is None:
            return OptionResolution("NO_OPTION_TRADE", (), "underlying_not_optionable")
        contracts = tuple(sorted(
            (r for r in self.listed_contracts if r.canonical_id == canonical_id
             and r.underlying.upper() == optionability.underlying.upper()
             and r.exchange.upper() == optionability.exchange.upper() and r.expiry >= day
             and r.source_snapshot_sha256 == optionability.source_snapshot_sha256),
            key=lambda r: (r.expiry, r.strike, r.right, r.tradingsymbol)))
        if not contracts:
            return OptionResolution("NO_OPTION_TRADE", (), "no_currently_listed_contract_in_source_snapshot")
        return OptionResolution("CONTRACTS_AVAILABLE", contracts, "source_snapshot_listed_contracts")


@dataclass(frozen=True)
class IndexMinute:
    ts: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class IndexBar15m:
    start: datetime
    end: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    minute_count: int = 15


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IndexMarketError("timestamps must be timezone-aware")
    return value.astimezone(IST)


def build_15m_bars(minutes: list[IndexMinute], *, as_of: datetime) -> list[IndexBar15m]:
    """Build only complete, exchange-aligned 15-minute bars available by the cutoff."""
    cutoff = _aware(as_of)
    by_time: dict[datetime, IndexMinute] = {}
    for row in minutes:
        ts, available = _aware(row.ts), _aware(row.available_at)
        if ts.second or ts.microsecond:  # minute precision is required
            raise IndexMarketError("minute timestamps must be aligned to whole minutes")
        if available < ts:
            raise IndexMarketError("observation cannot be available before its source timestamp")
        if not all(isfinite(float(v)) for v in (row.open, row.high, row.low, row.close)):
            raise IndexMarketError("OHLC must be finite")
        if (min(row.open, row.high, row.low, row.close) <= 0
                or row.high < max(row.open, row.close, row.low)
                or row.low > min(row.open, row.close, row.high)):
            raise IndexMarketError("invalid OHLC ordering")
        if ts.time() < SESSION_OPEN or ts.time() >= SESSION_CLOSE:
            continue
        if ts in by_time:
            raise IndexMarketError("duplicate index minute")
        by_time[ts] = row

    groups: dict[datetime, list[IndexMinute]] = {}
    for ts, row in by_time.items():
        session = datetime.combine(ts.date(), SESSION_OPEN, tzinfo=IST)
        offset = int((ts - session).total_seconds() // BAR.total_seconds())
        start = session + offset * BAR
        end = start + BAR
        if end.time() > SESSION_CLOSE:
            continue
        groups.setdefault(start, []).append(row)

    bars = []
    for start, rows in sorted(groups.items()):
        rows.sort(key=lambda r: _aware(r.ts))
        end = start + BAR
        expected = [start + timedelta(minutes=i) for i in range(15)]
        observed = [_aware(r.ts) for r in rows]
        available = max(_aware(r.available_at) for r in rows)
        if observed != expected or end > cutoff or available > cutoff:
            continue
        bars.append(IndexBar15m(start, end, available, rows[0].open,
                                max(r.high for r in rows), min(r.low for r in rows), rows[-1].close))
    return bars


def forward_return(bars: list[IndexBar15m], index: int, *, horizon_bars: int = 1) -> float:
    """Return close-to-close target for an exact same-session horizon; never a feature."""
    if horizon_bars not in (1, 2, 4):
        raise IndexMarketError("supported horizons are 15m, 30m and 60m")
    target = index + horizon_bars
    if index < 0 or target >= len(bars):
        raise IndexMarketError("future target is not available")
    base, future = bars[index], bars[target]
    if future.end - base.end != horizon_bars * BAR or future.end.date() != base.end.date():
        raise IndexMarketError("target bars must be contiguous and within the same session")
    return future.close / base.close - 1.0


def direction_label(realized_return: float, *, noise_threshold: float) -> str:
    """Apply a frozen, positive training-period noise threshold to a realized target."""
    if not isfinite(realized_return) or not isfinite(noise_threshold) or noise_threshold <= 0:
        raise IndexMarketError("finite realized return and positive frozen noise threshold required")
    if realized_return > noise_threshold:
        return "UP"
    if realized_return < -noise_threshold:
        return "DOWN"
    return "FLAT"
