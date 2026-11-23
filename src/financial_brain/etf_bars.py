"""Completed session bars from timestamped ETF quote events, with coverage diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from .forward_edge.market_events import EventKind, MarketEvent

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)
ALLOWED_MINUTES = (1, 5, 15, 30)


@dataclass(frozen=True)
class ETFBar:
    instrument_id: str
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume_change: int | None
    observations: int
    depth_coverage: float
    complete: bool
    source_event_ids: tuple[str, ...]


def completed_bars(events: Iterable[MarketEvent], *, as_of: datetime,
                   minutes: int = 5) -> list[ETFBar]:
    """Aggregate observed quotes into complete NSE-session bars without filling gaps.

    OHLC is sampled-quote OHLC, not exchange trade OHLC. Volume is a cumulative-volume
    difference only when the preceding minute is observed (except the first session bar).
    """
    if minutes not in ALLOWED_MINUTES:
        raise ValueError(f"minutes must be one of {ALLOWED_MINUTES}")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = as_of.astimezone(IST)
    bucket = timedelta(minutes=minutes)
    by_instrument: dict[str, dict[datetime, list[MarketEvent]]] = {}
    for event in events:
        if event.venue != "NSE" or event.source != "KITE_CONNECT_READ_ONLY":
            continue
        local = event.event_time.astimezone(IST)
        if local.date() != cutoff.date() or not OPEN <= local.time() < CLOSE:
            continue
        if event.available_at.astimezone(IST) > cutoff:
            continue
        price = event.payload.get("last_price", event.payload.get("price"))
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        minute_start = local.replace(second=0, microsecond=0)
        by_instrument.setdefault(event.instrument_id, {}).setdefault(minute_start, []).append(event)

    session_open = datetime.combine(cutoff.date(), OPEN, IST)
    session_close = datetime.combine(cutoff.date(), CLOSE, IST)
    result: list[ETFBar] = []
    for instrument, minute_events in sorted(by_instrument.items()):
        start = session_open
        while start + bucket <= min(session_close, cutoff):
            minute_starts = [start + timedelta(minutes=i) for i in range(minutes)]
            if all(m in minute_events for m in minute_starts):
                grouped = [event for m in minute_starts for event in sorted(
                    minute_events[m], key=lambda item: item.event_time)]
                prices = [float(e.payload.get("last_price", e.payload.get("price")))
                          for e in grouped]
                coverage = sum(
                    e.kind is EventKind.QUOTE and "bid" in e.payload and "ask" in e.payload
                    for e in grouped
                ) / len(grouped)
                volume_change = None
                end_volume = grouped[-1].payload.get("volume")
                if start == session_open:
                    if isinstance(end_volume, int) and end_volume >= 0:
                        volume_change = end_volume
                else:
                    baseline_rows = minute_events.get(start - timedelta(minutes=1), [])
                    baseline = (sorted(baseline_rows, key=lambda item: item.event_time)[-1]
                                if baseline_rows else None)
                    start_volume = baseline.payload.get("volume") if baseline else None
                    if (isinstance(start_volume, int) and isinstance(end_volume, int)
                            and end_volume >= start_volume):
                        volume_change = end_volume - start_volume
                result.append(ETFBar(
                    instrument, start, start + bucket, prices[0], max(prices), min(prices),
                    prices[-1], volume_change, len(grouped), coverage, True,
                    tuple(e.event_id for e in grouped),
                ))
            start += bucket
    return result
