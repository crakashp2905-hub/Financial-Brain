"""Permission-gated, source-hashed ETF reference records from exchange/AMC disclosures.

This module normalizes supplied reference rows; it does not fetch data or imply rights to
retain it. Research and retention require separate effective-dated permissions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum

from financial_brain.forward_edge.data_permissions import DataPermission, DataUse
from financial_brain.kernel.instruments import InstrumentId


class ETFReferenceError(ValueError):
    """ETF reference data is invalid, conflicting or not permissioned for its use."""


ALLOWED_SOURCES = frozenset({"NSE", "BSE", "AMC"})
ALLOWED_EXCHANGES = frozenset({"NSE", "BSE"})


class ETFExposureCategory(StrEnum):
    """Distinct ETF exposures; category is supplied by evidence, never guessed from symbol."""

    EQUITY_BROAD_MARKET = "EQUITY_BROAD_MARKET"
    EQUITY_SECTOR = "EQUITY_SECTOR"
    EQUITY_FACTOR = "EQUITY_FACTOR"
    BOND_DEBT = "BOND_DEBT"
    GOLD = "GOLD"
    SILVER = "SILVER"
    INTERNATIONAL = "INTERNATIONAL"
    OTHER_LISTED = "OTHER_LISTED"
    UNKNOWN = "UNKNOWN"


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ETFReferenceError(f"{field} must be timezone aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ETFReferenceRecord:
    """One source-versioned ETF listing/metadata row; facts are not backdated by observation."""

    source_id: str
    exchange: str
    symbol: str
    isin: str
    listing_date: date
    effective_from: date
    effective_to: date | None
    series: str
    published_at: datetime
    observed_at: datetime
    source_ref: str
    source_sha256: str
    underlying_index: str | None = None
    fund_house: str | None = None
    provider_token: str | None = None
    exposure_category: ETFExposureCategory = ETFExposureCategory.UNKNOWN

    def __post_init__(self) -> None:
        source = self.source_id.strip().upper()
        exchange = self.exchange.strip().upper()
        symbol = self.symbol.strip().upper()
        if source not in ALLOWED_SOURCES:
            raise ETFReferenceError("reference source must be NSE, BSE or AMC")
        if exchange not in ALLOWED_EXCHANGES:
            raise ETFReferenceError("ETF listing exchange must be NSE or BSE")
        if source in ALLOWED_EXCHANGES and source != exchange:
            raise ETFReferenceError("exchange source and listing exchange must agree")
        if not symbol or not self.series.strip() or not self.source_ref.strip():
            raise ETFReferenceError("symbol, exchange series and source reference are required")
        isin = self.isin.strip().upper()
        if len(isin) != 12 or not isin.isalnum():
            raise ETFReferenceError("ETF ISIN must be 12 alphanumeric characters")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ETFReferenceError("effective_to precedes effective_from")
        if self.listing_date > self.effective_from:
            raise ETFReferenceError("reference version cannot start before the exchange listing date")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ETFReferenceError("source_sha256 must be lowercase SHA-256")
        for field in ("underlying_index", "fund_house", "provider_token"):
            value = getattr(self, field)
            if value is not None:
                normalized = value.strip()
                if not normalized:
                    raise ETFReferenceError(f"{field} cannot be blank")
                object.__setattr__(self, field, normalized)
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "series", self.series.strip().upper())
        try:
            object.__setattr__(self, "exposure_category", ETFExposureCategory(self.exposure_category))
        except ValueError as exc:
            raise ETFReferenceError("ETF exposure category must be explicit and recognized") from exc
        object.__setattr__(self, "published_at", _aware(self.published_at, "published_at"))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        if self.observed_at < self.published_at:
            raise ETFReferenceError("reference was observed before publication")

    def instrument_id(self) -> InstrumentId:
        return InstrumentId(
            venue=self.exchange, market="IN", exchange=self.exchange,
            asset_class="ETF", symbol=self.symbol, isin=self.isin,
            provider_token=self.provider_token, effective_from=self.effective_from,
            effective_to=self.effective_to, underlying_index=self.underlying_index,
        )

    def content_sha256(self) -> str:
        body = {
            "source_id": self.source_id, "exchange": self.exchange, "symbol": self.symbol,
            "isin": self.isin, "listing_date": self.listing_date.isoformat(),
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "series": self.series, "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(), "source_ref": self.source_ref,
            "source_sha256": self.source_sha256, "underlying_index": self.underlying_index,
            "fund_house": self.fund_house, "provider_token": self.provider_token,
            "exposure_category": self.exposure_category.value,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ETFReferenceCatalog:
    """In-memory validated reference catalog; callers must provide both rights grants."""

    def __init__(self) -> None:
        self._records: list[ETFReferenceRecord] = []

    def add(self, record: ETFReferenceRecord, *, retention_permission: DataPermission,
            research_permission: DataPermission) -> None:
        as_of = record.observed_at.date()
        for permission, use in ((retention_permission, DataUse.RETENTION),
                                (research_permission, DataUse.RESEARCH)):
            if not permission.allows(source_id=record.source_id, use=use, on=as_of):
                raise ETFReferenceError(f"{use.value} permission is missing or ineffective for {record.source_id}")
        for prior in self._records:
            if (prior.exchange, prior.symbol) != (record.exchange, record.symbol):
                continue
            prior_start, record_start = prior.effective_from, record.effective_from
            prior_end, record_end = prior.effective_to or date.max, record.effective_to or date.max
            if max(prior_start, record_start) <= min(prior_end, record_end):
                raise ETFReferenceError("overlapping effective ETF listing identities")
        self._records.append(record)

    def resolve(self, *, exchange: str, symbol: str, as_of: datetime) -> ETFReferenceRecord:
        cutoff = _aware(as_of, "as_of")
        exchange_key, symbol_key = exchange.strip().upper(), symbol.strip().upper()
        matches = [row for row in self._records
                   if row.exchange == exchange_key and row.symbol == symbol_key
                   and row.effective_from <= cutoff.date()
                   and (row.effective_to is None or cutoff.date() <= row.effective_to)
                   and row.published_at <= cutoff and row.observed_at <= cutoff]
        if len(matches) != 1:
            reason = "unknown" if not matches else "ambiguous"
            raise ETFReferenceError(f"{reason} ETF identity {exchange_key}:{symbol_key} as of {cutoff.isoformat()}")
        return matches[0]

    def __len__(self) -> int:
        return len(self._records)


__all__ = ["ETFExposureCategory", "ETFReferenceCatalog", "ETFReferenceError", "ETFReferenceRecord"]
