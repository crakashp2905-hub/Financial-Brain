"""Fail-closed pre-entry checks for index forecasts and paper option decisions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .index_15m import check_probabilities
from .index_market import BAR, SESSION_CLOSE, SESSION_OPEN, IndexBar15m

_SHA = re.compile(r"^[0-9a-f]{64}$")
IST = ZoneInfo("Asia/Kolkata")


def _utc(value: datetime) -> datetime | None:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def index_forecast_preflight(*, probabilities: dict[str, float] | None,
                             bar: IndexBar15m | None, decision_at: datetime,
                             quote_observed_at: datetime | None,
                             quote_available_at: datetime | None,
                             max_quote_age: timedelta,
                             event_calendar_available_at: datetime | None,
                             event_calendar_source_sha256: str | None) -> dict[str, Any]:
    """Return READY only when forecast, complete bar, fresh quote and event data are causal.

    Invalid probabilities, missing/partial/forming bars, stale source data or an event
    calendar unavailable at decision time become explicit NO_TRADE reason codes.
    """
    reasons: set[str] = set()
    decision_utc = _utc(decision_at)
    if decision_utc is None:
        reasons.add("INVALID_DECISION_CLOCK")
    if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        reasons.add("INVALID_MAX_QUOTE_AGE_POLICY")
    if probabilities is None:
        reasons.add("PROBABILITIES_MISSING")
    else:
        try:
            check_probabilities(probabilities)
        except (ValueError, TypeError):
            reasons.add("IMPOSSIBLE_PROBABILITIES")

    if bar is None:
        reasons.add("PARTIAL_OR_MISSING_BAR")
    else:
        start_utc, end_utc, available_utc = (_utc(bar.start), _utc(bar.end), _utc(bar.available_at))
        valid_bar = (start_utc is not None and end_utc is not None and available_utc is not None
                     and bar.minute_count == 15 and bar.end - bar.start == BAR)
        if valid_bar and start_utc is not None and end_utc is not None and available_utc is not None:
            local_start, local_end = bar.start.astimezone(IST), bar.end.astimezone(IST)
            offset = (local_start - datetime.combine(local_start.date(), SESSION_OPEN,
                                                     tzinfo=IST)).total_seconds()
            valid_bar = (local_start.time() >= SESSION_OPEN and local_end.time() <= SESSION_CLOSE
                         and offset >= 0 and offset % BAR.total_seconds() == 0
                         and decision_utc is not None and end_utc <= decision_utc
                         and available_utc <= decision_utc)
        if not valid_bar:
            reasons.add("PARTIAL_FORMING_OR_FUTURE_BAR")

    quote_age_ms = None
    observed_utc = _utc(quote_observed_at) if quote_observed_at is not None else None
    available_utc = _utc(quote_available_at) if quote_available_at is not None else None
    if observed_utc is None or available_utc is None:
        reasons.add("QUOTE_SOURCE_CLOCK_MISSING")
    elif (decision_utc is not None and observed_utc is not None
          and available_utc is not None):
        if observed_utc > decision_utc or available_utc > decision_utc:
            reasons.add("QUOTE_SOURCE_CLOCK_NONCAUSAL")
        else:
            quote_age = decision_utc - available_utc
            quote_age_ms = int(quote_age.total_seconds() * 1000)
            if (isinstance(max_quote_age, timedelta)
                    and max_quote_age >= timedelta(0) and quote_age > max_quote_age):
                reasons.add("QUOTE_LAG_EXCEEDS_POLICY")

    event_utc = (_utc(event_calendar_available_at)
                 if event_calendar_available_at is not None else None)
    if (event_utc is None or not event_calendar_source_sha256
            or not _SHA.fullmatch(event_calendar_source_sha256)):
        reasons.add("PUBLISHED_EVENTS_UNAVAILABLE")
    elif decision_utc is not None and event_utc > decision_utc:
        reasons.add("PUBLISHED_EVENTS_UNAVAILABLE_AT_DECISION")

    ordered = tuple(sorted(reasons))
    return {"status": "NO_TRADE" if ordered else "READY",
            "reason_codes": ordered, "quote_age_ms": quote_age_ms,
            "bar_end": bar.end.isoformat() if bar else None,
            "decision_at": decision_utc.isoformat() if decision_utc else None,
            "event_calendar_source_sha256": event_calendar_source_sha256,
            "paper_only": True}
