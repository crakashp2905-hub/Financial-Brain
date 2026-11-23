"""Deterministic pre-signal checks for timestamped market-event feeds."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
class FeedQualityReport:
    venue: str
    instrument_id: str
    findings: tuple[FeedQualityFinding, ...]

    @property
    def ready(self) -> bool:
        return not self.findings


def audit_feed(
    events: list[MarketEvent], *, venue: str, instrument_id: str,
    session_open: datetime, session_close: datetime, as_of: datetime,
    bar_interval: timedelta = timedelta(minutes=1),
    max_tick_age: timedelta = timedelta(seconds=5),
) -> FeedQualityReport:
    """Find temporal, sequencing, bar-coverage, and quote-integrity defects.

    Events are inspected in caller-supplied arrival order. Bar event_time is
    treated as the interval start. The caller supplies the session calendar and
    interval because neither can safely be inferred from a candle payload.
    This is an audit report: callers must gate decisions on report.ready.
    """
    if not venue.strip() or not instrument_id.strip():
        raise FeedQualityError("venue and instrument_id are required")
    opened, closed, cutoff = (_utc(session_open, "session_open"),
                              _utc(session_close, "session_close"),
                              _utc(as_of, "as_of"))
    if opened >= closed or bar_interval <= timedelta(0) or max_tick_age < timedelta(0):
        raise FeedQualityError("session bounds and feed-quality intervals are invalid")

    scoped = [event for event in events
              if event.venue.upper() == venue.upper() and event.instrument_id == instrument_id]
    findings: list[FeedQualityFinding] = []
    previous_clock: dict[tuple[str, str, str], datetime] = {}
    previous_sequence: dict[tuple[str, str, str], int] = {}
    bars: dict[datetime, MarketEvent] = {}
    complete_bar_times: set[datetime] = set()

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
            if age > max_tick_age:
                findings.append(FeedQualityFinding(
                    "STALE_TICK", event.event_id,
                    f"source-clock age {age.total_seconds():.3f}s exceeds "
                    f"{max_tick_age.total_seconds():.3f}s"))
        if event.kind == EventKind.QUOTE:
            bid, ask = event.payload.get("bid"), event.payload.get("ask")
            if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > ask:
                findings.append(FeedQualityFinding(
                    "CROSSED_MARKET", event.event_id, f"bid {bid} exceeds ask {ask}"))
        if event.kind == EventKind.BAR:
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
            else:
                complete_bar_times.add(stamp)

    if cutoff >= opened + bar_interval and (not bars or min(bars) > opened):
        first = bars[min(bars)] if bars else None
        findings.append(FeedQualityFinding(
            "SESSION_OPEN_GAP", first.event_id if first else None,
            f"first bar starts {min(bars).isoformat() if first else 'none'}, "
            f"expected {opened.isoformat()}"))

    completed_until = min(cutoff, closed)
    expected = opened
    while expected + bar_interval <= completed_until:
        if expected not in complete_bar_times:
            findings.append(FeedQualityFinding(
                "MISSING_MINUTE", None, f"no completed bar starts at {expected.isoformat()}"))
        expected += bar_interval

    return FeedQualityReport(venue.upper(), instrument_id, tuple(findings))


__all__ = ["FeedQualityError", "FeedQualityFinding", "FeedQualityReport", "audit_feed"]
