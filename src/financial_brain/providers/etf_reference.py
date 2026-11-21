"""Permission-gated, source-hashed ETF reference records from exchange/AMC disclosures.

This module normalizes supplied reference rows; it does not fetch data or imply rights to
retain it. Research and retention require separate effective-dated permissions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
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


def _positive_decimal(value: Decimal | str | int, field: str) -> Decimal:
    try:
        normalized = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ETFReferenceError(f"{field} must be a finite positive decimal") from exc
    if not normalized.is_finite() or normalized <= 0:
        raise ETFReferenceError(f"{field} must be a finite positive decimal")
    return normalized


def _finite_decimal(value: Decimal | str | int, field: str) -> Decimal:
    try:
        normalized = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ETFReferenceError(f"{field} must be a finite decimal") from exc
    if not normalized.is_finite():
        raise ETFReferenceError(f"{field} must be a finite decimal")
    return normalized


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
    inception_date: date | None = None
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
        if self.inception_date is not None and self.inception_date > self.listing_date:
            raise ETFReferenceError("ETF inception cannot follow its first exchange listing")
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
            "inception_date": self.inception_date.isoformat() if self.inception_date else None,
            "series": self.series, "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(), "source_ref": self.source_ref,
            "source_sha256": self.source_sha256, "underlying_index": self.underlying_index,
            "fund_house": self.fund_house, "provider_token": self.provider_token,
            "exposure_category": self.exposure_category.value,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ETFValuationObservation:
    """One source-hashed NAV or iNAV point, visible only after its publication time."""

    source_id: str
    exchange: str
    symbol: str
    isin: str
    valuation_type: str
    valuation_date: date
    value_per_unit: Decimal
    published_at: datetime
    observed_at: datetime
    source_ref: str
    source_sha256: str

    def __post_init__(self) -> None:
        source = self.source_id.strip().upper()
        exchange = self.exchange.strip().upper()
        symbol = self.symbol.strip().upper()
        isin = self.isin.strip().upper()
        valuation_type = self.valuation_type.strip().upper()
        if source not in ALLOWED_SOURCES or exchange not in ALLOWED_EXCHANGES:
            raise ETFReferenceError("valuation source and exchange must identify NSE, BSE or AMC")
        if source in ALLOWED_EXCHANGES and source != exchange:
            raise ETFReferenceError("exchange source and valuation exchange must agree")
        if not symbol or len(isin) != 12 or not isin.isalnum():
            raise ETFReferenceError("valuation requires symbol and a 12-character alphanumeric ISIN")
        if valuation_type not in {"NAV", "INAV"}:
            raise ETFReferenceError("valuation_type must be NAV or INAV")
        if not self.source_ref.strip():
            raise ETFReferenceError("valuation source reference is required")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ETFReferenceError("source_sha256 must be lowercase SHA-256")
        published_at, observed_at = _aware(self.published_at, "published_at"), _aware(self.observed_at, "observed_at")
        if observed_at < published_at:
            raise ETFReferenceError("valuation was observed before publication")
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "valuation_type", valuation_type)
        object.__setattr__(self, "value_per_unit", _positive_decimal(self.value_per_unit, "value_per_unit"))
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "observed_at", observed_at)

    def content_sha256(self) -> str:
        body = {
            "source_id": self.source_id, "exchange": self.exchange, "symbol": self.symbol,
            "isin": self.isin, "valuation_type": self.valuation_type,
            "valuation_date": self.valuation_date.isoformat(),
            "value_per_unit": str(self.value_per_unit), "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(), "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ETFTrackingMetrics:
    """Effective-dated tracking and fund-cost figures from an authorized disclosure."""

    source_id: str
    exchange: str
    symbol: str
    isin: str
    effective_from: date
    effective_to: date | None
    tracking_difference_bps: Decimal
    tracking_error_bps: Decimal
    expense_ratio_bps: Decimal
    published_at: datetime
    observed_at: datetime
    source_ref: str
    source_sha256: str

    def __post_init__(self) -> None:
        source = self.source_id.strip().upper()
        exchange = self.exchange.strip().upper()
        symbol = self.symbol.strip().upper()
        isin = self.isin.strip().upper()
        if source not in ALLOWED_SOURCES or exchange not in ALLOWED_EXCHANGES:
            raise ETFReferenceError("tracking metrics require a recognized NSE/BSE/AMC source and exchange")
        if source in ALLOWED_EXCHANGES and source != exchange:
            raise ETFReferenceError("exchange source and tracking-metric exchange must agree")
        if (not symbol or len(isin) != 12 or not isin.isalnum()
                or (self.effective_to is not None and self.effective_to < self.effective_from)):
            raise ETFReferenceError("tracking metrics require a valid identity and effective interval")
        if not self.source_ref.strip():
            raise ETFReferenceError("tracking-metric source reference is required")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ETFReferenceError("source_sha256 must be lowercase SHA-256")
        diff = _finite_decimal(self.tracking_difference_bps, "tracking_difference_bps")
        error = _finite_decimal(self.tracking_error_bps, "tracking_error_bps")
        expense = _finite_decimal(self.expense_ratio_bps, "expense_ratio_bps")
        if error < 0 or expense < 0:
            raise ETFReferenceError("tracking error and expense ratio cannot be negative")
        published_at, observed_at = _aware(self.published_at, "published_at"), _aware(self.observed_at, "observed_at")
        if observed_at < published_at:
            raise ETFReferenceError("tracking metrics were observed before publication")
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "tracking_difference_bps", diff)
        object.__setattr__(self, "tracking_error_bps", error)
        object.__setattr__(self, "expense_ratio_bps", expense)
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "observed_at", observed_at)

    def content_sha256(self) -> str:
        body = {
            "source_id": self.source_id, "exchange": self.exchange, "symbol": self.symbol,
            "isin": self.isin, "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "tracking_difference_bps": str(self.tracking_difference_bps),
            "tracking_error_bps": str(self.tracking_error_bps),
            "expense_ratio_bps": str(self.expense_ratio_bps),
            "published_at": self.published_at.isoformat(), "observed_at": self.observed_at.isoformat(),
            "source_ref": self.source_ref, "source_sha256": self.source_sha256,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ETFPrimaryMarketContext:
    """Issuer/AP creation-redemption record, explicitly informational for retail trading."""

    source_id: str
    exchange: str
    symbol: str
    isin: str
    activity: str
    context_date: date
    quantity: Decimal
    quantity_unit: str
    published_at: datetime
    observed_at: datetime
    source_ref: str
    source_sha256: str

    def __post_init__(self) -> None:
        source = self.source_id.strip().upper()
        exchange = self.exchange.strip().upper()
        symbol = self.symbol.strip().upper()
        isin = self.isin.strip().upper()
        activity = self.activity.strip().upper()
        unit = self.quantity_unit.strip().upper()
        if source not in ALLOWED_SOURCES or exchange not in ALLOWED_EXCHANGES:
            raise ETFReferenceError("primary-market context requires a recognized NSE/BSE/AMC source")
        if source in ALLOWED_EXCHANGES and source != exchange:
            raise ETFReferenceError("exchange source and primary-market exchange must agree")
        if not symbol or len(isin) != 12 or not isin.isalnum():
            raise ETFReferenceError("primary-market context requires a valid ETF identity")
        if activity not in {"CREATION", "REDEMPTION"} or unit not in {"UNITS", "INR_NOTIONAL"}:
            raise ETFReferenceError("activity must be CREATION/REDEMPTION and unit UNITS/INR_NOTIONAL")
        if not self.source_ref.strip():
            raise ETFReferenceError("primary-market source reference is required")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ETFReferenceError("source_sha256 must be lowercase SHA-256")
        quantity = _positive_decimal(self.quantity, "quantity")
        published_at, observed_at = _aware(self.published_at, "published_at"), _aware(self.observed_at, "observed_at")
        if observed_at < published_at:
            raise ETFReferenceError("primary-market context was observed before publication")
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "activity", activity)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "quantity_unit", unit)
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "observed_at", observed_at)

    @property
    def retail_order_actionable(self) -> bool:
        return False

    @property
    def use_label(self) -> str:
        return "ISSUER_MARKET_MAKER_REFERENCE_CONTEXT_ONLY"

    def content_sha256(self) -> str:
        body = {
            "source_id": self.source_id, "exchange": self.exchange, "symbol": self.symbol,
            "isin": self.isin, "activity": self.activity, "context_date": self.context_date.isoformat(),
            "quantity": str(self.quantity), "quantity_unit": self.quantity_unit,
            "published_at": self.published_at.isoformat(), "observed_at": self.observed_at.isoformat(),
            "source_ref": self.source_ref, "source_sha256": self.source_sha256,
            "use_label": self.use_label,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ETFReferenceCatalog:
    """In-memory validated reference catalog; callers must provide both rights grants."""

    def __init__(self) -> None:
        self._records: list[ETFReferenceRecord] = []
        self._valuations: list[ETFValuationObservation] = []
        self._tracking_metrics: list[ETFTrackingMetrics] = []
        self._primary_market_context: list[ETFPrimaryMarketContext] = []

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

    def add_valuation(
        self,
        observation: ETFValuationObservation,
        *,
        retention_permission: DataPermission,
        research_permission: DataPermission,
    ) -> None:
        as_of = observation.observed_at.date()
        for permission, use in ((retention_permission, DataUse.RETENTION),
                                (research_permission, DataUse.RESEARCH)):
            if not permission.allows(source_id=observation.source_id, use=use, on=as_of):
                raise ETFReferenceError(f"{use.value} permission is missing or ineffective for {observation.source_id}")
        identity = self.resolve(
            exchange=observation.exchange,
            symbol=observation.symbol,
            as_of=observation.observed_at,
        )
        if identity.isin != observation.isin:
            raise ETFReferenceError("valuation ISIN does not match the point-in-time ETF identity")
        self._valuations.append(observation)

    def add_tracking_metrics(
        self,
        metrics: ETFTrackingMetrics,
        *,
        retention_permission: DataPermission,
        research_permission: DataPermission,
    ) -> None:
        as_of = metrics.observed_at.date()
        for permission, use in ((retention_permission, DataUse.RETENTION),
                                (research_permission, DataUse.RESEARCH)):
            if not permission.allows(source_id=metrics.source_id, use=use, on=as_of):
                raise ETFReferenceError(f"{use.value} permission is missing or ineffective for {metrics.source_id}")
        identity = self.resolve(exchange=metrics.exchange, symbol=metrics.symbol, as_of=metrics.observed_at)
        if identity.isin != metrics.isin:
            raise ETFReferenceError("tracking-metric ISIN does not match the point-in-time ETF identity")
        self._tracking_metrics.append(metrics)

    def add_primary_market_context(
        self,
        context: ETFPrimaryMarketContext,
        *,
        retention_permission: DataPermission,
        research_permission: DataPermission,
    ) -> None:
        as_of = context.observed_at.date()
        for permission, use in ((retention_permission, DataUse.RETENTION),
                                (research_permission, DataUse.RESEARCH)):
            if not permission.allows(source_id=context.source_id, use=use, on=as_of):
                raise ETFReferenceError(f"{use.value} permission is missing or ineffective for {context.source_id}")
        identity = self.resolve(exchange=context.exchange, symbol=context.symbol, as_of=context.observed_at)
        if identity.isin != context.isin:
            raise ETFReferenceError("primary-market context ISIN does not match the point-in-time ETF identity")
        self._primary_market_context.append(context)

    def primary_market_context_as_of(
        self, *, exchange: str, symbol: str, isin: str, as_of: datetime
    ) -> list[ETFPrimaryMarketContext]:
        cutoff = _aware(as_of, "as_of")
        identity = self.resolve(exchange=exchange, symbol=symbol, as_of=cutoff)
        if identity.isin != isin.strip().upper():
            raise ETFReferenceError("requested context does not match the point-in-time ETF identity")
        return sorted(
            (row for row in self._primary_market_context
             if row.exchange == exchange.strip().upper() and row.symbol == symbol.strip().upper()
             and row.isin == isin.strip().upper() and row.context_date <= cutoff.date()
             and row.published_at <= cutoff and row.observed_at <= cutoff),
            key=lambda row: (row.context_date, row.published_at, row.observed_at),
        )

    def resolve_tracking_metrics(
        self, *, exchange: str, symbol: str, isin: str, as_of: datetime
    ) -> ETFTrackingMetrics:
        cutoff = _aware(as_of, "as_of")
        identity = self.resolve(exchange=exchange, symbol=symbol, as_of=cutoff)
        if identity.isin != isin.strip().upper():
            raise ETFReferenceError("requested metrics do not match the point-in-time ETF identity")
        rows = [row for row in self._tracking_metrics
                if row.exchange == exchange.strip().upper()
                and row.symbol == symbol.strip().upper()
                and row.isin == isin.strip().upper()
                and row.effective_from <= cutoff.date()
                and (row.effective_to is None or cutoff.date() <= row.effective_to)
                and row.published_at <= cutoff and row.observed_at <= cutoff]
        if not rows:
            raise ETFReferenceError("no published ETF tracking metrics are effective as of the requested time")
        rows.sort(key=lambda row: (row.published_at, row.observed_at), reverse=True)
        if (len(rows) > 1 and (rows[0].published_at, rows[0].observed_at)
                == (rows[1].published_at, rows[1].observed_at)
                and rows[0].content_sha256() != rows[1].content_sha256()):
            raise ETFReferenceError("conflicting ETF tracking metrics share the same publication clocks")
        return rows[0]

    def resolve_valuation(
        self,
        *,
        exchange: str,
        symbol: str,
        isin: str,
        valuation_type: str,
        valuation_date: date,
        as_of: datetime,
    ) -> ETFValuationObservation:
        cutoff = _aware(as_of, "as_of")
        identity = self.resolve(exchange=exchange, symbol=symbol, as_of=cutoff)
        if identity.isin != isin.strip().upper():
            raise ETFReferenceError("requested valuation ISIN does not match point-in-time ETF identity")
        matches = [row for row in self._valuations
                   if row.exchange == exchange.strip().upper()
                   and row.symbol == symbol.strip().upper()
                   and row.isin == isin.strip().upper()
                   and row.valuation_type == valuation_type.strip().upper()
                   and row.valuation_date == valuation_date
                   and row.published_at <= cutoff and row.observed_at <= cutoff]
        if not matches:
            raise ETFReferenceError("no published ETF valuation is available as of the requested time")
        return max(matches, key=lambda row: (row.published_at, row.observed_at))

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


__all__ = [
    "ETFExposureCategory", "ETFReferenceCatalog", "ETFReferenceError",
    "ETFPrimaryMarketContext", "ETFReferenceRecord", "ETFTrackingMetrics", "ETFValuationObservation",
]
