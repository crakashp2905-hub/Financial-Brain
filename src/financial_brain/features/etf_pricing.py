"""Fail-closed ETF pricing state; NAV and EOD values are never quote substitutes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent


def resolve_etf_paper_price(
    quote: MarketEvent | None,
    *,
    venue: str,
    instrument_id: str,
    decision_time: datetime,
    max_quote_age: timedelta,
) -> dict[str, Any]:
    """Use only a current executable quote; absence, staleness or bad depth is UNPRICED."""
    venue = venue.strip().upper()
    if venue not in {"NSE", "BSE"} or not instrument_id.startswith(f"{venue}:"):
        raise ValueError("ETF identity must be qualified with the matching NSE/BSE venue")
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone aware")
    if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        raise ValueError("max_quote_age must be a non-negative duration")
    if quote is None:
        return _unpriced("NO_OBSERVED_QUOTE")
    if quote.venue != venue or quote.instrument_id != instrument_id:
        return _unpriced("QUOTE_IDENTITY_MISMATCH")
    if quote.kind is not EventKind.QUOTE or executable_quote(quote) is None:
        return _unpriced("NO_EXECUTABLE_TWO_SIDED_DEPTH")
    decision = decision_time.astimezone(timezone.utc)
    if quote.available_at > decision:
        return _unpriced("QUOTE_NOT_YET_AVAILABLE")
    age = decision - quote.event_time
    if age < timedelta(0):
        return _unpriced("QUOTE_EVENT_TIME_IN_FUTURE")
    if age > max_quote_age:
        return _unpriced("STALE_QUOTE")
    executable = executable_quote(quote)
    assert executable is not None
    return {
        "fill_status": "QUOTE_PRICED",
        "price_state": "PRICED_FROM_OBSERVED_QUOTE",
        "price_source": quote.source,
        "bid": str(executable["bid"]),
        "ask": str(executable["ask"]),
        "quote_event_time": quote.event_time.isoformat(),
        "quote_available_at": quote.available_at.isoformat(),
        "age_seconds": age.total_seconds(),
        "quote_sha256": quote.sha256(),
    }


def _unpriced(reason: str) -> dict[str, Any]:
    return {
        "fill_status": "UNPRICED",
        "price_state": "UNPRICED",
        "price_source": None,
        "bid": None,
        "ask": None,
        "reason": reason,
        "fill_created": False,
    }


__all__ = ["resolve_etf_paper_price"]
