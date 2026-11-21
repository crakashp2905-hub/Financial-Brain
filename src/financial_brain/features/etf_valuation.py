"""Synchronous, freshness-gated quote versus indicative-NAV comparison."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.providers.etf_reference import (
    ETFReferenceError,
    ETFReferenceRecord,
    ETFValuationObservation,
)


def compare_quote_to_inav(
    quote: MarketEvent,
    *,
    identity: ETFReferenceRecord,
    inav: ETFValuationObservation,
    decision_time: datetime,
    quote_max_age: timedelta,
    inav_max_age: timedelta,
    max_observation_skew: timedelta,
) -> dict[str, Any]:
    """Return midquote premium/discount in bps or refuse an invalid comparison."""
    durations = (quote_max_age, inav_max_age, max_observation_skew)
    if any(not isinstance(value, timedelta) or value < timedelta(0) for value in durations):
        raise ETFReferenceError("valuation freshness and synchronization windows must be non-negative")
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ETFReferenceError("decision_time must be timezone aware")
    decision = decision_time.astimezone(timezone.utc)
    if quote.kind is not EventKind.QUOTE or executable_quote(quote) is None:
        raise ETFReferenceError("a two-sided quote is required for comparison")
    if (quote.venue != identity.exchange or quote.instrument_id != f"{identity.exchange}:{identity.symbol}"
            or inav.exchange != identity.exchange or inav.symbol != identity.symbol
            or inav.isin != identity.isin or inav.valuation_type != "INAV"):
        raise ETFReferenceError("quote and iNAV must match the same point-in-time ETF identity")
    if quote.available_at > decision or inav.published_at > decision or inav.observed_at > decision:
        raise ETFReferenceError("quote or iNAV was unavailable at the decision time")
    quote_age = decision - quote.event_time
    inav_age = decision - inav.observed_at
    observation_skew = abs(quote.event_time - inav.observed_at)
    if quote_age < timedelta(0) or inav_age < timedelta(0):
        raise ETFReferenceError("future observation cannot be compared")
    if quote_age > quote_max_age:
        raise ETFReferenceError("quote is stale for ETF valuation comparison")
    if inav_age > inav_max_age:
        raise ETFReferenceError("iNAV is stale for ETF valuation comparison")
    if observation_skew > max_observation_skew:
        raise ETFReferenceError("quote and iNAV observations are asynchronous")
    try:
        bid, ask = Decimal(str(quote.payload["bid"])), Decimal(str(quote.payload["ask"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ETFReferenceError("two-sided quote prices are invalid") from exc
    if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask <= bid:
        raise ETFReferenceError("two-sided quote prices are invalid")
    midpoint = (bid + ask) / Decimal(2)
    premium_bps = (midpoint / inav.value_per_unit - Decimal(1)) * Decimal(10000)
    return {
        "instrument_id": quote.instrument_id,
        "isin": identity.isin,
        "quote_source": quote.source,
        "inav_source": inav.source_id,
        "quote_event_time": quote.event_time.isoformat(),
        "quote_available_at": quote.available_at.isoformat(),
        "inav_published_at": inav.published_at.isoformat(),
        "inav_observed_at": inav.observed_at.isoformat(),
        "quote_age_seconds": quote_age.total_seconds(),
        "inav_age_seconds": inav_age.total_seconds(),
        "observation_skew_seconds": observation_skew.total_seconds(),
        "midquote": str(midpoint),
        "inav": str(inav.value_per_unit),
        "premium_discount_bps": format(premium_bps.normalize(), "f"),
        "quote_sha256": quote.sha256(),
        "inav_sha256": inav.content_sha256(),
    }


__all__ = ["compare_quote_to_inav"]


def detect_etf_valuation_anomalies(
    *,
    expected_sessions: list[date],
    navs: list[ETFValuationObservation],
    inavs: list[ETFValuationObservation],
    premium_comparisons: list[dict[str, Any]],
    underlying_open_by_session: dict[date, bool | None],
    as_of: datetime,
    max_nav_age: timedelta,
    max_inav_age: timedelta,
    max_repeated_nav_sessions: int,
    max_abs_premium_discount_bps: Decimal | str | int,
) -> list[dict[str, Any]]:
    """Find missing/stale NAV evidence, extreme synchronized premium and sourced closures.

    Input NAV/iNAV rows must already be selected as the point-in-time revisions by the
    catalog. The function reports gaps and never carries yesterday's value forward.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ETFReferenceError("as_of must be timezone aware")
    if max_nav_age < timedelta(0) or max_inav_age < timedelta(0):
        raise ETFReferenceError("valuation age thresholds cannot be negative")
    if max_repeated_nav_sessions < 1:
        raise ETFReferenceError("max_repeated_nav_sessions must be positive")
    try:
        premium_limit = Decimal(max_abs_premium_discount_bps)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ETFReferenceError("premium threshold must be finite and non-negative") from exc
    if not premium_limit.is_finite() or premium_limit < 0:
        raise ETFReferenceError("premium threshold must be finite and non-negative")
    cutoff = as_of.astimezone(timezone.utc)
    sessions = sorted(set(expected_sessions))
    nav_by_day = {row.valuation_date: row for row in navs if row.valuation_type == "NAV"}
    inav_by_day = {row.valuation_date: row for row in inavs if row.valuation_type == "INAV"}
    if len(nav_by_day) != len([row for row in navs if row.valuation_type == "NAV"]):
        raise ETFReferenceError("resolve NAV revisions before anomaly analysis")
    if len(inav_by_day) != len([row for row in inavs if row.valuation_type == "INAV"]):
        raise ETFReferenceError("resolve iNAV revisions before anomaly analysis")
    findings: list[dict[str, Any]] = []
    for day in sessions:
        if day not in nav_by_day:
            findings.append({"type": "NAV_GAP", "session_date": day.isoformat()})
        if day not in inav_by_day:
            findings.append({"type": "INAV_GAP", "session_date": day.isoformat()})
        if underlying_open_by_session.get(day) is False:
            findings.append({"type": "UNDERLYING_MARKET_CLOSED", "session_date": day.isoformat()})

    if nav_by_day:
        latest_nav = max(nav_by_day.values(), key=lambda row: (row.valuation_date, row.published_at))
        if latest_nav.observed_at > cutoff or latest_nav.published_at > cutoff:
            findings.append({"type": "NAV_NOT_YET_AVAILABLE", "session_date": latest_nav.valuation_date.isoformat()})
        elif cutoff - latest_nav.observed_at > max_nav_age:
            findings.append({"type": "NAV_STALE", "session_date": latest_nav.valuation_date.isoformat(),
                             "age_seconds": (cutoff - latest_nav.observed_at).total_seconds()})
        streak = 1
        previous_value = None
        for day in sessions:
            row = nav_by_day.get(day)
            if row is None:
                streak = 1
                previous_value = None
                continue
            if row.value_per_unit == previous_value:
                streak += 1
            else:
                streak = 1
            previous_value = row.value_per_unit
            if streak > max_repeated_nav_sessions:
                findings.append({"type": "NAV_REPEATED_ACROSS_SESSIONS", "session_date": day.isoformat(),
                                 "repeated_sessions": streak})

    if inav_by_day:
        latest_inav = max(inav_by_day.values(), key=lambda row: (row.observed_at, row.published_at))
        if latest_inav.observed_at > cutoff or latest_inav.published_at > cutoff:
            findings.append({"type": "INAV_NOT_YET_AVAILABLE", "session_date": latest_inav.valuation_date.isoformat()})
        elif cutoff - latest_inav.observed_at > max_inav_age:
            findings.append({"type": "INAV_STALE", "session_date": latest_inav.valuation_date.isoformat(),
                             "age_seconds": (cutoff - latest_inav.observed_at).total_seconds()})

    ist = ZoneInfo("Asia/Kolkata")
    for comparison in premium_comparisons:
        try:
            premium = Decimal(str(comparison["premium_discount_bps"]))
            quote_time = datetime.fromisoformat(str(comparison["quote_event_time"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if not premium.is_finite():
            continue
        if abs(premium) > premium_limit:
            findings.append({"type": "LARGE_PREMIUM_DISCOUNT", "session_date": quote_time.astimezone(ist).date().isoformat(),
                             "premium_discount_bps": format(premium.normalize(), "f")})
    return findings


__all__.append("detect_etf_valuation_anomalies")
