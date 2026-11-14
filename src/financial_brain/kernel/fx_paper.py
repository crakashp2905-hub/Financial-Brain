"""Fail-closed, quote-driven FX paper-intent simulator for Indian ETCDs.

The simulator consumes explicit contract, venue-authorization, exposure and quote evidence.
It never discovers authorization or prices itself and cannot reach a broker.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


class FxPaperError(ValueError):
    """Malformed FX paper contract, evidence or quote."""


PAIRS = {"USD-INR", "EUR-INR", "GBP-INR", "JPY-INR", "EUR-USD", "GBP-USD", "USD-JPY"}
PRODUCTS = {"FUTURE", "CALL", "PUT"}
SUPPORTED_VENUES = {"NSE", "BSE", "MSEI"}
RBI_RULE_SOURCE = "https://rbi.org.in/scripts/FS_Notification.aspx?Id=12594&Mode=0&fn=6"
RBI_EFFECTIVE_DATE = date(2024, 5, 3)
MAX_AUTHORIZATION_AGE_DAYS = 30


def _digest(value: str, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise FxPaperError(f"{label} must be a lowercase SHA-256")


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FxPaperError(f"{label} must be timezone-aware")


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    # Contract expiry is a day in a month; clamp a month-end boundary to that month's end.
    from calendar import monthrange

    return date(year, month, min(day.day, monthrange(year, month)[1]))


def rbi_rulebook_sha256() -> str:
    """Stable digest for reviewed RBI exchange-traded FX constraints in this module."""
    rules = {"pairs": sorted(PAIRS), "products": sorted(PRODUCTS),
             "supported_venues": sorted(SUPPORTED_VENUES),
             "effective_from": RBI_EFFECTIVE_DATE.isoformat(), "source": RBI_RULE_SOURCE,
             "max_tenor_months": 12, "inr_pair_purpose": "CONTRACTED_EXPOSURE_HEDGE",
             "options": "EUROPEAN_ON_SPOT", "settlement_currency": "INR"}
    return hashlib.sha256(json.dumps(rules, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FxContractSpec:
    contract_id: str
    jurisdiction: str
    venue_id: str
    pair: str
    product: str
    expiry: date
    effective_from: date
    effective_through: date
    lot_size: int
    units_per_lot: Decimal
    settlement_currency: str
    underlying_kind: str
    exercise_style: str
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.contract_id.strip() or not self.venue_id.strip() or not self.source_id.strip():
            raise FxPaperError("contract, venue and source identities are required")
        if not self.jurisdiction.strip() or not self.pair.strip() or not self.product.strip():
            raise FxPaperError("jurisdiction, pair and product fields are required")
        if (type(self.expiry) is not date or type(self.effective_from) is not date
                or type(self.effective_through) is not date
                or type(self.lot_size) is not int or self.lot_size <= 0):
            raise FxPaperError("contract effective dates, expiry and positive integer lot size are required")
        if not isinstance(self.units_per_lot, Decimal) or not self.units_per_lot.is_finite() or self.units_per_lot <= 0:
            raise FxPaperError("units_per_lot must be a positive finite Decimal")
        _digest(self.source_sha256, "contract source")


@dataclass(frozen=True)
class FxVenueAuthorization:
    venue_id: str
    jurisdiction: str
    venue_kind: str
    authorized: bool
    effective_from: date
    effective_through: date
    checked_at: datetime
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.venue_id.strip() or not self.source_id.strip():
            raise FxPaperError("venue authorization identity and source are required")
        if type(self.effective_from) is not date or type(self.effective_through) is not date:
            raise FxPaperError("venue authorization requires effective dates")
        _aware(self.checked_at, "venue authorization checked_at")
        _digest(self.source_sha256, "venue authorization source")


@dataclass(frozen=True)
class FxExposureEvidence:
    pair: str
    units: Decimal
    exposure_id: str
    contracted: bool
    unhedged: bool
    valid_on: date
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.exposure_id.strip() or not self.source_id.strip():
            raise FxPaperError("exposure identity and source are required")
        if type(self.valid_on) is not date:
            raise FxPaperError("exposure evidence valid_on must be a date")
        if not isinstance(self.units, Decimal) or not self.units.is_finite() or self.units <= 0:
            raise FxPaperError("exposure units must be a positive finite Decimal")
        _digest(self.source_sha256, "exposure source")


@dataclass(frozen=True)
class FxQuote:
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    available_at: datetime
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, Decimal) and value.is_finite() and value > 0
                   for value in (self.bid, self.ask)) or self.ask < self.bid:
            raise FxPaperError("FX bid/ask must be positive finite decimals with ask >= bid")
        _aware(self.observed_at, "quote observed_at")
        _aware(self.available_at, "quote available_at")
        if not self.source_id.strip():
            raise FxPaperError("quote source_id is required")
        _digest(self.source_sha256, "quote source")


@dataclass(frozen=True)
class FxPaperResult:
    status: Literal["NO_TRADE", "PAPER_SIMULATED"]
    reasons: tuple[str, ...]
    contract_id: str
    side: str
    quantity_lots: int
    simulated_price: str | None
    estimated_spread_cost_inr: str | None
    evidence_sha256: str


def simulate_fx_paper_intent(
    contract: FxContractSpec,
    *,
    venue_authorization: FxVenueAuthorization | None,
    quote: FxQuote | None,
    side: Literal["BUY", "SELL"],
    quantity_lots: int,
    as_of: datetime,
    exposure: FxExposureEvidence | None = None,
    max_quote_age_seconds: int = 60,
) -> FxPaperResult:
    """Return NO_TRADE unless RBI-rule, venue, contract, exposure and quote evidence qualify.

    A qualifying result models a conservative top-of-book fill (buy at ask, sell at bid).
    It remains a paper simulation and says nothing about real exchange execution.
    """
    _aware(as_of, "as_of")
    if side not in {"BUY", "SELL"} or type(quantity_lots) is not int or quantity_lots <= 0:
        raise FxPaperError("side must be BUY/SELL and quantity_lots must be a positive integer")
    if type(max_quote_age_seconds) is not int or max_quote_age_seconds <= 0:
        raise FxPaperError("max_quote_age_seconds must be a positive integer")

    reasons: set[str] = set()
    session_day = as_of.date()
    if contract.jurisdiction != "IN":
        reasons.add("UNKNOWN_OR_UNSUPPORTED_JURISDICTION")
    if contract.pair not in PAIRS or contract.product not in PRODUCTS:
        reasons.add("UNKNOWN_OR_UNSUPPORTED_INSTRUMENT")
    if not contract.effective_from <= session_day <= contract.effective_through:
        reasons.add("CONTRACT_SPEC_OUT_OF_DATE")
    if contract.settlement_currency != "INR":
        reasons.add("INVALID_SETTLEMENT_CURRENCY")
    if contract.product in {"CALL", "PUT"} and (
        contract.underlying_kind != "SPOT" or contract.exercise_style != "EUROPEAN"
    ):
        reasons.add("INVALID_FX_OPTION_TERMS")
    if contract.product == "FUTURE" and contract.exercise_style != "NONE":
        reasons.add("INVALID_FX_FUTURES_TERMS")
    if session_day < RBI_EFFECTIVE_DATE:
        reasons.add("NO_EFFECTIVE_RBI_RULE")
    if contract.expiry <= session_day or contract.expiry > _add_months(session_day, 12):
        reasons.add("INVALID_OR_OVER_12_MONTH_TENOR")
    venue = venue_authorization
    if venue is None:
        reasons.add("MISSING_VENUE_AUTHORIZATION")
    else:
        if venue.venue_id not in SUPPORTED_VENUES:
            reasons.add("UNKNOWN_OR_UNSUPPORTED_VENUE")
        if venue.jurisdiction != contract.jurisdiction or venue.venue_id != contract.venue_id:
            reasons.add("VENUE_IDENTITY_MISMATCH")
        if venue.venue_kind != "RECOGNIZED_STOCK_EXCHANGE":
            reasons.add("UNSUPPORTED_VENUE_KIND")
        if venue.authorized is not True:
            reasons.add("VENUE_NOT_AUTHORIZED")
        if venue.effective_from > session_day or venue.effective_through < session_day:
            reasons.add("VENUE_AUTHORIZATION_OUT_OF_DATE")
        if venue.checked_at.date() > session_day:
            reasons.add("FUTURE_VENUE_AUTHORIZATION_EVIDENCE")
        if (session_day - venue.checked_at.date()).days > MAX_AUTHORIZATION_AGE_DAYS:
            reasons.add("STALE_VENUE_AUTHORIZATION_EVIDENCE")

    if "INR" in contract.pair:
        if exposure is None:
            reasons.add("MISSING_CONTRACTED_FX_EXPOSURE")
        elif (exposure.contracted is not True or exposure.unhedged is not True
              or exposure.valid_on != session_day
              or exposure.pair != contract.pair
              or exposure.units < contract.units_per_lot * quantity_lots):
            reasons.add("INVALID_OR_STALE_CONTRACTED_FX_EXPOSURE")

    if quote is None:
        reasons.add("MISSING_FX_QUOTE")
    else:
        age = (as_of - quote.available_at).total_seconds()
        if quote.available_at > as_of or quote.observed_at > as_of:
            reasons.add("FUTURE_FX_QUOTE")
        elif age > max_quote_age_seconds:
            reasons.add("STALE_FX_QUOTE")
        if quote.observed_at > quote.available_at:
            reasons.add("QUOTE_AVAILABILITY_PRECEDES_OBSERVATION")

    canonical_evidence = {
        "contract": asdict(contract),
        "venue_authorization": asdict(venue) if venue else None,
        "quote": asdict(quote) if quote else None,
        "exposure": asdict(exposure) if exposure else None,
        "as_of": as_of.isoformat(), "side": side, "quantity_lots": quantity_lots,
        "rule_source": RBI_RULE_SOURCE,
        "rule_effective_from": RBI_EFFECTIVE_DATE.isoformat(),
    }
    canonical_evidence["rule_source_sha256"] = rbi_rulebook_sha256()
    digest = hashlib.sha256(json.dumps(canonical_evidence, sort_keys=True, default=str,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    ordered_reasons = tuple(sorted(reasons))
    if ordered_reasons:
        return FxPaperResult("NO_TRADE", ordered_reasons, contract.contract_id, side,
                             quantity_lots, None, None, digest)
    assert quote is not None
    price = quote.ask if side == "BUY" else quote.bid
    spread_cost = (quote.ask - quote.bid) * contract.units_per_lot * quantity_lots / 2
    return FxPaperResult("PAPER_SIMULATED", (), contract.contract_id, side, quantity_lots,
                         str(price), str(spread_cost), digest)
