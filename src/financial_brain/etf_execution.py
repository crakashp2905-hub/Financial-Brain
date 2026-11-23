"""Quote-only ETF paper fills; NAV and daily bars are never execution fallbacks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .forward_edge.market_data import executable_quote
from .forward_edge.market_events import MarketEvent


@dataclass(frozen=True)
class ETFQuoteFill:
    status: str
    filled_units: int
    price: Decimal | None
    source_event_id: str | None
    reason: str | None


def resolve_etf_quote_fill(*, quote: MarketEvent | None, side: str, requested_units: int,
                           execution_time: datetime,
                           max_quote_age: timedelta = timedelta(seconds=5)) -> ETFQuoteFill:
    """Resolve from a current executable quote only; unavailable prices stay UNPRICED."""
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    if type(requested_units) is not int or requested_units <= 0:
        raise ValueError("requested_units must be a positive integer")
    if execution_time.tzinfo is None or execution_time.utcoffset() is None:
        raise ValueError("execution_time must be timezone-aware")
    if max_quote_age < timedelta(0):
        raise ValueError("max_quote_age cannot be negative")
    if quote is None:
        return ETFQuoteFill("UNPRICED", 0, None, None, "QUOTE_MISSING")
    if quote.available_at > execution_time:
        return ETFQuoteFill("UNPRICED", 0, None, quote.event_id, "QUOTE_NOT_YET_AVAILABLE")
    age = execution_time - quote.event_time
    if age < timedelta(0) or age > max_quote_age:
        return ETFQuoteFill("UNPRICED", 0, None, quote.event_id, "QUOTE_STALE_OR_FUTURE")
    executable = executable_quote(quote)
    if executable is None:
        return ETFQuoteFill("UNPRICED", 0, None, quote.event_id, "QUOTE_NOT_EXECUTABLE")
    levels = executable["depth"]["sell" if side == "BUY" else "buy"]
    remaining = requested_units
    notional = Decimal(0)
    filled = 0
    for level in levels:
        price = Decimal(str(level["price"]))
        quantity = int(level["quantity"])
        take = min(remaining, quantity)
        notional += price * take
        filled += take
        remaining -= take
        if remaining == 0:
            break
    if filled == 0:
        return ETFQuoteFill("UNPRICED", 0, None, quote.event_id, "NO_VISIBLE_DEPTH")
    return ETFQuoteFill("FILLED" if remaining == 0 else "PARTIAL", filled,
                        notional / filled, quote.event_id,
                        None if remaining == 0 else "VISIBLE_DEPTH_LIMIT")
