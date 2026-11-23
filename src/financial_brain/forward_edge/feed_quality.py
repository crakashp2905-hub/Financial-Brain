"""Deterministic pre-signal checks for timestamped market-event feeds."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math

from financial_brain.forward_edge.market_events import EventKind, MarketEvent


class FeedQualityError(ValueError):
    """Feed-quality audit inputs do not define a valid session contract."""


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeedQualityError(f"{label} must be timezone aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FeedQualityFinding:
    code: str
    event_id: str | None
    detail: str


@dataclass(frozen=True)
class FeedQualityMetrics:
    """Instrument/session quality ratios; absent denominators are represented as None."""

    expected_bars: int
    complete_bars: int
    bar_coverage_fraction: float | None
    tick_count: int
    late_tick_count: int
    late_tick_rate: float | None
    quote_count: int
    quote_age_p50_seconds: float | None
    quote_age_p95_seconds: float | None
    quote_age_max_seconds: float | None
    depth_quote_count: int
    valid_depth_quote_count: int
    depth_validity_fraction: float | None
    inconsistent_ohlc_count: int
    strike_selection_count: int
    strike_selection_mae: float | None


@dataclass(frozen=True)
class FeedQualityReport:
    venue: str
    instrument_id: str
    session_date: date
    findings: tuple[FeedQualityFinding, ...]
    metrics: FeedQualityMetrics

    @property
    def ready(self) -> bool:
        return not self.findings


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _valid_quote_depth(event: MarketEvent) -> bool:
    payload = event.payload
    bid, ask = _number(payload.get("bid")), _number(payload.get("ask"))
    bid_qty, ask_qty = _number(payload.get("bid_quantity")), _number(payload.get("ask_quantity"))
    depth = payload.get("depth")
    if (bid is None or ask is None or bid <= 0 or ask <= bid or bid_qty is None or ask_qty is None
            or bid_qty <= 0 or ask_qty <= 0 or not isinstance(depth, dict)):
        return False
    bids, asks = depth.get("buy"), depth.get("sell")
    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
        return False

    def parse_level(row: object) -> tuple[float, float] | None:
        if isinstance(row, dict):
            price, quantity = _number(row.get("price")), _number(row.get("quantity"))
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            price, quantity = _number(row[0]), _number(row[1])
        else:
            return None
        if price is None or quantity is None or price <= 0 or quantity <= 0:
            return None
        return price, quantity

    bid_levels = [parse_level(row) for row in bids]
    ask_levels = [parse_level(row) for row in asks]
    if any(level is None for level in bid_levels + ask_levels):
        return False
    valid_bids = [level for level in bid_levels if level is not None]
    valid_asks = [level for level in ask_levels if level is not None]
    return (
        all(valid_bids[i][0] >= valid_bids[i + 1][0] for i in range(len(valid_bids) - 1))
        and all(valid_asks[i][0] <= valid_asks[i + 1][0] for i in range(len(valid_asks) - 1))
        and valid_bids[0] == (bid, bid_qty)
        and valid_asks[0] == (ask, ask_qty)
    )


def _inconsistent_ohlc(event: MarketEvent) -> bool:
    payload = event.payload
    values = [_number(payload.get(key)) for key in ("open", "high", "low", "close", "volume")]
    if any(value is None for value in values):
        return True
    opened, high, low, close, volume = (value for value in values if value is not None)
    return volume < 0 or high < max(opened, close, low) or low > min(opened, close, high)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def audit_feed(
    events: list[MarketEvent], *, venue: str, instrument_id: str,
    session_open: datetime, session_close: datetime, as_of: datetime,
    bar_interval: timedelta = timedelta(minutes=1),
    max_tick_age: timedelta = timedelta(seconds=5),
    late_tick_threshold: timedelta = timedelta(seconds=1),
) -> FeedQualityReport:
    """Find temporal, sequencing, bar-coverage, and quote-integrity defects.

    Events are inspected in caller-supplied arrival order. Bar event_time is
    treated as the interval start. The caller supplies the session calendar and
    interval because neither can safely be inferred from a candle payload.
    This is an audit report: callers must gate decisions on report.ready.
    """
    if not venue.strip() or not instrument_id.strip():
        raise FeedQualityError("venue and instrument_id are required")
    session_date = session_open.date()
    opened, closed, cutoff = (_utc(session_open, "session_open"),
                              _utc(session_close, "session_close"),
                              _utc(as_of, "as_of"))
    if (opened >= closed or bar_interval <= timedelta(0) or max_tick_age < timedelta(0)
            or late_tick_threshold < timedelta(0)):
        raise FeedQualityError("session bounds and feed-quality intervals are invalid")

    scoped = [event for event in events
              if event.venue.upper() == venue.upper() and event.instrument_id == instrument_id]
    findings: list[FeedQualityFinding] = []
    previous_clock: dict[tuple[str, str, str], datetime] = {}
    previous_sequence: dict[tuple[str, str, str], int] = {}
    bars: dict[datetime, MarketEvent] = {}
    complete_bar_times: set[datetime] = set()
    tick_delays: list[float] = []
    late_tick_count = 0
    quote_ages: list[float] = []
    depth_quote_count = 0
    valid_depth_quote_count = 0
    inconsistent_ohlc_count = 0
    strike_errors: list[float] = []

    for event in scoped:
        stream = (event.source, event.venue, event.instrument_id)
        source_clock = _utc(event.exchange_at or event.source_at or event.event_time,
                            "event source clock")
        prior_clock = previous_clock.get(stream)
        if prior_clock is not None and source_clock < prior_clock:
            findings.append(FeedQualityFinding(
                "NONMONOTONIC_TIMESTAMP", event.event_id,
                f"source clock {source_clock.isoformat()} follows {prior_clock.isoformat()}"))
        previous_clock[stream] = source_clock

        if event.sequence is not None:
            prior_sequence = previous_sequence.get(stream)
            if prior_sequence is not None:
                if event.sequence <= prior_sequence:
                    findings.append(FeedQualityFinding(
                        "OUT_OF_SEQUENCE", event.event_id,
                        f"sequence {event.sequence} does not follow {prior_sequence}"))
                elif event.sequence > prior_sequence + 1:
                    findings.append(FeedQualityFinding(
                        "SEQUENCE_GAP", event.event_id,
                        f"sequence advanced from {prior_sequence} to {event.sequence}"))
            previous_sequence[stream] = event.sequence

        if event.available_at > cutoff:
            findings.append(FeedQualityFinding(
                "NOT_AVAILABLE_AT_AS_OF", event.event_id,
                f"available_at {event.available_at.isoformat()} exceeds audit cutoff"))
        if event.kind in (EventKind.TICK, EventKind.QUOTE):
            age = cutoff - source_clock
            usable_at_cutoff = event.available_at <= cutoff
            if event.kind == EventKind.TICK and usable_at_cutoff:
                delay = (event.received_at - source_clock).total_seconds()
                tick_delays.append(delay)
                if event.received_at - source_clock > late_tick_threshold:
                    late_tick_count += 1
            elif event.kind == EventKind.QUOTE and usable_at_cutoff:
                quote_ages.append(age.total_seconds())
            if age > max_tick_age:
                findings.append(FeedQualityFinding(
                    "STALE_TICK", event.event_id,
                    f"source-clock age {age.total_seconds():.3f}s exceeds "
                    f"{max_tick_age.total_seconds():.3f}s"))
        if event.kind == EventKind.QUOTE and event.available_at <= cutoff:
            depth_quote_count += 1
            if _valid_quote_depth(event):
                valid_depth_quote_count += 1
            bid, ask = event.payload.get("bid"), event.payload.get("ask")
            if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > ask:
                findings.append(FeedQualityFinding(
                    "CROSSED_MARKET", event.event_id, f"bid {bid} exceeds ask {ask}"))
        selected_strike = (_number(event.payload.get("selected_strike"))
                           if event.available_at <= cutoff else None)
        target_strike = (_number(event.payload.get("target_strike"))
                         if event.available_at <= cutoff else None)
        if selected_strike is not None and target_strike is not None:
            if selected_strike > 0 and target_strike > 0:
                strike_errors.append(abs(selected_strike - target_strike) / target_strike)
            else:
                findings.append(FeedQualityFinding(
                    "INVALID_STRIKE_SELECTION", event.event_id,
                    "selected_strike and target_strike must both be positive"))
        if event.kind == EventKind.BAR:
            bad_ohlc = _inconsistent_ohlc(event)
            if bad_ohlc:
                inconsistent_ohlc_count += 1
                findings.append(FeedQualityFinding(
                    "INCONSISTENT_OHLC", event.event_id, "bar OHLCV values are inconsistent"))
            stamp = event.event_time
            if stamp < opened or stamp >= closed:
                findings.append(FeedQualityFinding(
                    "BAR_OUTSIDE_SESSION", event.event_id, stamp.isoformat()))
                continue
            offset = stamp - opened
            if offset % bar_interval != timedelta(0):
                findings.append(FeedQualityFinding(
                    "WRONG_INTERVAL_ANCHOR", event.event_id,
                    f"bar starts {offset.total_seconds():g}s after session open; "
                    f"interval is {bar_interval.total_seconds():g}s"))
            if event.available_at > cutoff:
                continue
            bars[stamp] = event
            if event.payload.get("is_complete") is False or event.payload.get("complete") is False:
                findings.append(FeedQualityFinding(
                    "PARTIAL_CANDLE", event.event_id, "provider marks the candle incomplete"))
            elif stamp + bar_interval > cutoff:
                findings.append(FeedQualityFinding(
                    "PARTIAL_CANDLE", event.event_id,
                    "candle interval has not ended by the audit cutoff"))
            elif not bad_ohlc:
                complete_bar_times.add(stamp)

    if cutoff >= opened + bar_interval and (not bars or min(bars) > opened):
        first = bars[min(bars)] if bars else None
        findings.append(FeedQualityFinding(
            "SESSION_OPEN_GAP", first.event_id if first else None,
            f"first bar starts {min(bars).isoformat() if first else 'none'}, "
            f"expected {opened.isoformat()}"))

    completed_until = min(cutoff, closed)
    expected = opened
    expected_count = 0
    while expected + bar_interval <= completed_until:
        expected_count += 1
        if expected not in complete_bar_times:
            findings.append(FeedQualityFinding(
                "MISSING_MINUTE", None, f"no completed bar starts at {expected.isoformat()}"))
        expected += bar_interval

    tick_count = len(tick_delays)
    quote_count = len(quote_ages)
    metrics = FeedQualityMetrics(
        expected_bars=expected_count,
        complete_bars=len(complete_bar_times),
        bar_coverage_fraction=(len(complete_bar_times) / expected_count if expected_count else None),
        tick_count=tick_count,
        late_tick_count=late_tick_count,
        late_tick_rate=late_tick_count / tick_count if tick_count else None,
        quote_count=quote_count,
        quote_age_p50_seconds=_percentile(quote_ages, 0.50),
        quote_age_p95_seconds=_percentile(quote_ages, 0.95),
        quote_age_max_seconds=max(quote_ages) if quote_ages else None,
        depth_quote_count=depth_quote_count,
        valid_depth_quote_count=valid_depth_quote_count,
        depth_validity_fraction=(valid_depth_quote_count / depth_quote_count
                                  if depth_quote_count else None),
        inconsistent_ohlc_count=inconsistent_ohlc_count,
        strike_selection_count=len(strike_errors),
        strike_selection_mae=(sum(strike_errors) / len(strike_errors) if strike_errors else None),
    )
    return FeedQualityReport(venue.upper(), instrument_id, session_date, tuple(findings), metrics)


__all__ = ["FeedQualityError", "FeedQualityFinding", "FeedQualityMetrics",
           "FeedQualityReport", "audit_feed"]
