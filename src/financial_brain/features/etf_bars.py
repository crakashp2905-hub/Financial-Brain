"""Session-bounded ETF bars from the normalized event archive contract."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.venue_calendar import VenueCalendar

SUPPORTED_INTERVALS = frozenset({1, 5, 15, 30, 60})


def aggregate_session_bars(
    events: list[MarketEvent],
    *,
    venue: str,
    instrument_id: str,
    session_date: date,
    calendar: VenueCalendar,
    interval_minutes: int,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """Aggregate observed ETF ticks/quotes only inside a sourced session and as-of cutoff.

    The first cumulative-volume observation has no known prior sample and contributes zero.
    Missing buckets are absent; no close or NAV is copied into an unobserved bar.
    """
    if interval_minutes not in SUPPORTED_INTERVALS:
        raise ValueError(f"interval_minutes must be one of {sorted(SUPPORTED_INTERVALS)}")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    venue = venue.strip().upper()
    if venue not in {"NSE", "BSE"} or not instrument_id.startswith(f"{venue}:"):
        raise ValueError("instrument identity must be qualified with the matching NSE/BSE venue")
    session_open, session_close = calendar.session_bounds_utc(venue, session_date)
    cutoff = min(as_of.astimezone(timezone.utc), session_close)
    span = interval_minutes * 60
    grouped: dict[int, list[tuple[datetime, int, str, float, datetime, str, str]]] = defaultdict(list)
    prior_volume: int | None = None
    ordered = sorted(events, key=lambda event: (event.event_time, event.sequence or -1, event.event_id))
    for event in ordered:
        if event.venue != venue or event.instrument_id != instrument_id:
            continue
        if event.kind not in (EventKind.TICK, EventKind.QUOTE):
            continue
        event_time = event.event_time.astimezone(timezone.utc)
        available_at = event.available_at.astimezone(timezone.utc)
        if not (session_open <= event_time < session_close and available_at <= cutoff):
            continue
        raw_price = event.payload.get("last_price") if event.kind is EventKind.QUOTE else event.payload.get("price")
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
            continue
        try:
            price = float(raw_price)
            volume = int(event.payload["volume"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(price) or price <= 0 or volume < 0:
            continue
        delta = 0 if prior_volume is None or volume < prior_volume else volume - prior_volume
        prior_volume = volume
        bucket = int((event_time - session_open).total_seconds() // span)
        grouped[bucket].append((event_time, delta, event.source, price, available_at,
                                event.event_id, event.sha256()))

    bars: list[dict[str, Any]] = []
    for bucket, rows in sorted(grouped.items()):
        prices = [row[3] for row in rows]
        bars.append({
            "venue": venue,
            "instrument_id": instrument_id,
            "session_date": session_date.isoformat(),
            "interval_minutes": interval_minutes,
            "bucket_start": (session_open + timedelta(seconds=bucket * span)).isoformat(),
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": sum(int(row[1] or 0) for row in rows),
            "observations": len(rows),
            "sources": sorted({row[2] for row in rows}),
            "available_at": max(row[4] for row in rows).isoformat(),
            "source_event_ids": [row[5] for row in rows],
            "input_sha256": hashlib.sha256(json.dumps(
                [(row[5], row[6]) for row in rows], separators=(",", ":"), allow_nan=False
            ).encode("utf-8")).hexdigest(),
        })
    return bars


__all__ = ["SUPPORTED_INTERVALS", "aggregate_session_bars"]
