"""Point-in-time comparison of an ETF quote with a source-attributed iNAV observation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .forward_edge.market_events import EventKind, MarketEvent


@dataclass(frozen=True)
class ETFValuationComparison:
    status: str
    premium_discount_pct: Decimal | None
    market_mid: Decimal | None
    indicative_nav: Decimal | None
    quote_time: datetime | None
    inav_time: datetime | None
    timestamp_skew: timedelta | None
    reason: str | None


@dataclass(frozen=True)
class ETFValuationHealth:
    """Operational NAV/iNAV health; closure context is never inferred from price staleness."""
    status: str
    nav_age_days: int | None
    inav_age: timedelta | None
    premium_discount_pct: Decimal | None
    premium_flag: str | None
    international_market_context: str
    reasons: tuple[str, ...]


def assess_valuation_health(*, nav: Decimal | None, nav_as_of,
                            indicative_nav: Decimal | None,
                            indicative_nav_at: datetime | None,
                            market_mid: Decimal | None,
                            decision_time: datetime,
                            large_premium_pct: Decimal = Decimal("1.0"),
                            nav_stale_after_days: int = 3,
                            inav_stale_after: timedelta = timedelta(seconds=5),
                            international_market_closed: bool | None = None
                            ) -> ETFValuationHealth:
    """Flag stale values and large premiums; unknown closure calendars stay UNKNOWN."""
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    if large_premium_pct < 0 or nav_stale_after_days < 0 or inav_stale_after < timedelta(0):
        raise ValueError("health thresholds cannot be negative")
    local_date = decision_time.astimezone(ZoneInfo("Asia/Kolkata")).date()
    nav_age_days = (local_date - nav_as_of).days if nav_as_of is not None else None
    inav_age = decision_time - indicative_nav_at if indicative_nav_at else None
    reasons: list[str] = []
    if nav is None or nav_as_of is None:
        reasons.append("NAV_MISSING")
    elif nav_age_days < 0:
        reasons.append("NAV_FUTURE_DATED")
    elif nav_age_days > nav_stale_after_days:
        reasons.append("NAV_STALE_MULTIDAY")
    if indicative_nav is None or indicative_nav_at is None:
        reasons.append("INAV_MISSING")
    elif indicative_nav_at.tzinfo is None or indicative_nav_at.utcoffset() is None:
        reasons.append("INAV_TIMEZONE_MISSING")
    elif inav_age < timedelta(0):
        reasons.append("INAV_FUTURE_DATED")
    elif inav_age > inav_stale_after:
        reasons.append("INAV_STALE")
    premium = None
    if market_mid is not None and indicative_nav is not None and indicative_nav > 0:
        premium = (market_mid / indicative_nav - Decimal(1)) * Decimal(100)
    premium_flag = None
    if premium is not None and abs(premium) >= large_premium_pct:
        premium_flag = "LARGE_PREMIUM" if premium > 0 else "LARGE_DISCOUNT"
        reasons.append(premium_flag)
    closure = ("CLOSED" if international_market_closed is True else
               "OPEN" if international_market_closed is False else "UNKNOWN")
    if closure == "CLOSED":
        reasons.append("UNDERLYING_MARKET_CLOSED")
    return ETFValuationHealth(
        status="ALERT" if reasons else "HEALTHY", nav_age_days=nav_age_days,
        inav_age=inav_age, premium_discount_pct=premium, premium_flag=premium_flag,
        international_market_context=closure, reasons=tuple(reasons))


def latest_inav_for_symbol(con, instrument_id: str, decision_time: datetime) -> dict | None:
    """Resolve the latest published source-attributed iNAV for an NSE ETF symbol."""
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    try:
        venue, symbol = instrument_id.split(":", 1)
    except ValueError:
        raise ValueError("instrument_id must be EXCHANGE:SYMBOL") from None
    if venue.upper() != "NSE" or not symbol:
        raise ValueError("ETF iNAV lookup supports NSE:SYMBOL only")
    local_date = decision_time.astimezone(ZoneInfo("Asia/Kolkata")).date()
    ref = con.execute(
        """SELECT isin FROM etf_reference_observations
           WHERE exchange='NSE' AND symbol=? AND observed_on<=?
           ORDER BY observed_on DESC LIMIT 1""", [symbol, local_date],
    ).fetchone()
    if not ref:
        return None
    row = con.execute(
        """SELECT indicative_nav, indicative_nav_at, source_published_at, source_url,
                  source_document_sha256, observed_on
           FROM etf_product_disclosures
           WHERE isin=? AND observed_on<=? AND source_published_at<=?
             AND indicative_nav IS NOT NULL AND indicative_nav_at IS NOT NULL
           ORDER BY indicative_nav_at DESC, source_published_at DESC LIMIT 1""",
        [ref[0], local_date, decision_time],
    ).fetchone()
    if not row:
        return None
    keys = ("indicative_nav", "indicative_nav_at", "source_published_at", "source_url",
            "source_document_sha256", "observed_on")
    return dict(zip(keys, row))


def compare_latest_for_quote(con, quote: MarketEvent, *, decision_time: datetime,
                             max_quote_age: timedelta = timedelta(seconds=5),
                             max_inav_age: timedelta = timedelta(seconds=5),
                             max_timestamp_skew: timedelta = timedelta(seconds=2)
                             ) -> ETFValuationComparison:
    """Resolve the point-in-time iNAV from the reference DB and compare to one quote."""
    inav = latest_inav_for_symbol(con, quote.instrument_id, decision_time)
    return compare_quote_to_inav(
        quote, inav or {}, decision_time=decision_time, max_quote_age=max_quote_age,
        max_inav_age=max_inav_age, max_timestamp_skew=max_timestamp_skew)


def compare_quote_to_inav(
    quote: MarketEvent,
    inav: dict,
    *,
    decision_time: datetime,
    max_quote_age: timedelta = timedelta(seconds=5),
    max_inav_age: timedelta = timedelta(seconds=5),
    max_timestamp_skew: timedelta = timedelta(seconds=2),
) -> ETFValuationComparison:
    """Compare a two-sided quote midpoint with iNAV only when both are synchronized.

    Unavailable, stale, future-dated, or asynchronous inputs return a reason and no
    premium/discount. The comparison is informational and does not imply executable alpha.
    """
    empty = ETFValuationComparison("REFUSED", None, None, None, None, None, None, None)
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    if any(delta < timedelta(0) for delta in
           (max_quote_age, max_inav_age, max_timestamp_skew)):
        raise ValueError("freshness and skew limits cannot be negative")
    if quote.kind is not EventKind.QUOTE:
        return ETFValuationComparison(**{**empty.__dict__, "reason": "QUOTE_NOT_TWO_SIDED"})
    if quote.available_at > decision_time:
        return ETFValuationComparison(**{**empty.__dict__, "reason": "QUOTE_NOT_YET_AVAILABLE"})
    source_url = str((inav or {}).get("source_url", ""))
    source_hash = str((inav or {}).get("source_document_sha256", ""))
    if (urlparse(source_url).scheme != "https" or not urlparse(source_url).netloc
            or re.fullmatch(r"[0-9a-fA-F]{64}", source_hash) is None):
        return ETFValuationComparison(**{**empty.__dict__, "reason": "INAV_PROVENANCE_MISSING"})
    inav_time = inav.get("indicative_nav_at")
    published_at = inav.get("source_published_at")
    if not isinstance(inav_time, datetime) or not isinstance(published_at, datetime):
        return ETFValuationComparison(**{**empty.__dict__, "reason": "INAV_TIMESTAMP_MISSING"})
    if any(value.tzinfo is None or value.utcoffset() is None
           for value in (inav_time, published_at)):
        return ETFValuationComparison(**{**empty.__dict__, "reason": "INAV_TIMEZONE_MISSING"})
    if published_at > decision_time or inav_time > decision_time:
        return ETFValuationComparison("REFUSED", None, None, None, quote.event_time,
                                      inav_time, abs(quote.event_time - inav_time),
                                      "INAV_NOT_YET_AVAILABLE")
    quote_age = decision_time - quote.event_time
    inav_age = decision_time - inav_time
    skew = abs(quote.event_time - inav_time)
    if quote_age < timedelta(0) or inav_age < timedelta(0):
        reason = "FUTURE_TIMESTAMP"
    elif quote_age > max_quote_age:
        reason = "STALE_QUOTE"
    elif inav_age > max_inav_age:
        reason = "STALE_INAV"
    elif skew > max_timestamp_skew:
        reason = "ASYNCHRONOUS_OBSERVATIONS"
    else:
        reason = None
    if reason:
        return ETFValuationComparison("REFUSED", None, None, None, quote.event_time,
                                      inav_time, skew, reason)
    try:
        bid = Decimal(str(quote.payload["bid"]))
        ask = Decimal(str(quote.payload["ask"]))
        nav = Decimal(str(inav["indicative_nav"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return ETFValuationComparison("REFUSED", None, None, None, quote.event_time,
                                      inav_time, skew, "PRICE_MISSING_OR_INVALID")
    if not bid.is_finite() or not ask.is_finite() or not nav.is_finite() or \
            bid <= 0 or ask < bid or nav <= 0:
        return ETFValuationComparison("REFUSED", None, None, None, quote.event_time,
                                      inav_time, skew, "PRICE_MISSING_OR_INVALID")
    mid = (bid + ask) / Decimal(2)
    premium = (mid / nav - Decimal(1)) * Decimal(100)
    return ETFValuationComparison("COMPARABLE", premium, mid, nav, quote.event_time,
                                  inav_time, skew, None)
