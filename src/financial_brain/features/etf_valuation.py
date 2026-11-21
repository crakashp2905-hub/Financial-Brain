"""Synchronous, freshness-gated quote versus indicative-NAV comparison."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

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
