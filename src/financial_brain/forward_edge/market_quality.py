"""Source-hashed per-instrument/day data quality summaries for the quote archive."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.market_events import EventKind, MarketEvent, MarketEventError

IST = ZoneInfo("Asia/Kolkata")


class MarketQualityError(ValueError):
    """Quality report inputs are missing or malformed."""


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def build_instrument_day_quality_report(
    events: Sequence[MarketEvent],
    *,
    venue: str,
    instrument_id: str,
    session_day: date,
    as_of: datetime,
    expected_event_count: int | None,
    max_lateness_ms: int = 1_000,
    max_quote_age_ms: int = 5_000,
    strike_selection_evidence: Sequence[dict[str, Any]] = (),
    require_strike_selection: bool = False,
) -> dict[str, Any]:
    """Summarize coverage, late ticks, OHLC, executable depth, quote age and strike error.

    Strike selection rows must include target/selected strike and a source SHA-256. The
    report never fills absent measurements with zero; unavailable metrics stay explicit.
    """
    if not venue.strip() or not instrument_id.strip():
        raise MarketQualityError("venue and instrument ID are required")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise MarketQualityError("as_of must be timezone aware")
    if expected_event_count is not None and (type(expected_event_count) is not int or expected_event_count < 1):
        raise MarketQualityError("expected_event_count must be a positive integer when supplied")
    if type(max_lateness_ms) is not int or max_lateness_ms < 0:
        raise MarketQualityError("max_lateness_ms must be a non-negative integer")
    if type(max_quote_age_ms) is not int or max_quote_age_ms < 0:
        raise MarketQualityError("max_quote_age_ms must be a non-negative integer")

    cutoff = as_of.astimezone(timezone.utc)
    rows = [event for event in events
            if event.venue == venue and event.instrument_id == instrument_id
            and event.event_time.astimezone(IST).date() == session_day
            and event.available_at <= cutoff]
    source_hashes: list[str] = []
    invalid_ohlc_ids: list[str] = []
    invalid_event_ids: list[str] = []
    received_lags: list[float] = []
    executable_quotes: list[bool] = []
    quote_ages: list[float] = []
    ticks = [event for event in rows if event.kind is EventKind.TICK]

    for event in rows:
        try:
            MarketEvent.from_dict(event.as_dict())
        except (MarketEventError, TypeError, ValueError):
            invalid_event_ids.append(event.event_id)
            if event.kind is EventKind.BAR:
                invalid_ohlc_ids.append(event.event_id)
        try:
            source_hashes.append(event.sha256())
        except (TypeError, ValueError) as exc:
            raise MarketQualityError(f"event {event.event_id} cannot be canonically hashed") from exc
        received_lags.append((event.received_at - event.event_time).total_seconds() * 1_000)
        if event.kind is EventKind.QUOTE:
            quote_ages.append((cutoff - event.event_time).total_seconds() * 1_000)
            from financial_brain.forward_edge.market_data import executable_quote

            executable_quotes.append(executable_quote(event) is not None)

    late_ticks = sum(
        (event.received_at - event.event_time).total_seconds() * 1_000 > max_lateness_ms
        for event in ticks
    )
    negative_lag_ids = [event.event_id for event in rows if event.received_at < event.event_time]
    expected = expected_event_count
    coverage = min(1.0, len(rows) / expected) if expected else None

    selection_errors: list[tuple[Decimal, Decimal]] = []
    selection_hashes: list[str] = []
    for row in strike_selection_evidence:
        try:
            target = Decimal(str(row["target_strike"]))
            selected = Decimal(str(row["selected_strike"]))
            source_sha = str(row["source_sha256"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise MarketQualityError("strike selection evidence is malformed") from exc
        if (not target.is_finite() or not selected.is_finite() or target <= 0 or selected <= 0
                or len(source_sha) != 64 or any(c not in "0123456789abcdef" for c in source_sha)):
            raise MarketQualityError("strike selection evidence requires positive strikes and source SHA-256")
        selection_errors.append((abs(selected - target), target))
        selection_hashes.append(source_sha)

    if selection_errors:
        absolute_errors = [float(error) for error, _ in selection_errors]
        relative_errors = [float(error / target) for error, target in selection_errors]
        strike_selection = {
            "status": "ASSESSED", "sample_count": len(selection_errors),
            "mean_absolute_error": sum(absolute_errors) / len(absolute_errors),
            "max_absolute_error": max(absolute_errors),
            "mean_relative_error": sum(relative_errors) / len(relative_errors),
            "max_relative_error": max(relative_errors),
            "source_hashes": sorted(selection_hashes),
        }
    else:
        strike_selection = {
            "status": "REQUIRED_MISSING" if require_strike_selection else "NOT_APPLICABLE",
            "sample_count": 0, "mean_absolute_error": None, "max_absolute_error": None,
            "mean_relative_error": None, "max_relative_error": None, "source_hashes": [],
        }

    issues = []
    if expected is None:
        issues.append("EXPECTED_EVENT_DENOMINATOR_MISSING")
    elif len(rows) < expected:
        issues.append("EVENT_COVERAGE_BELOW_EXPECTATION")
    if not rows:
        issues.append("NO_AVAILABLE_EVENTS")
    if invalid_event_ids:
        issues.append("INVALID_OR_MUTATED_EVENT_PAYLOAD")
    if negative_lag_ids:
        issues.append("SOURCE_CLOCK_AFTER_RECEIPT")
    if require_strike_selection and not selection_errors:
        issues.append("STRIKE_SELECTION_EVIDENCE_MISSING")
    report: dict[str, Any] = {
        "schema": "fb.instrument_day_market_quality.v1",
        "venue": venue, "instrument_id": instrument_id, "session_day": session_day.isoformat(),
        "as_of_utc": cutoff.isoformat(), "status": "COMPLETE" if not issues else "INCOMPLETE",
        "issues": sorted(set(issues)), "event_count": len(rows), "expected_event_count": expected,
        "event_coverage_fraction": coverage,
        "late_tick_rate": late_ticks / len(ticks) if ticks else None,
        "late_tick_count": late_ticks, "tick_count": len(ticks),
        "inconsistent_ohlc_count": len(invalid_ohlc_ids),
        "invalid_event_count": len(invalid_event_ids),
        "invalid_event_ids": sorted(invalid_event_ids),
        "negative_clock_event_ids": sorted(negative_lag_ids),
        "mean_receive_lag_ms": sum(received_lags) / len(received_lags) if received_lags else None,
        "max_receive_lag_ms": max(received_lags) if received_lags else None,
        "executable_quote_fraction": (sum(executable_quotes) / len(executable_quotes)
                                      if executable_quotes else None),
        "executable_quote_count": sum(executable_quotes), "quote_count": len(executable_quotes),
        "quote_age_ms_p50": _percentile(quote_ages, 0.50),
        "quote_age_ms_p95": _percentile(quote_ages, 0.95),
        "quote_age_ms_max": max(quote_ages) if quote_ages else None,
        "stale_quote_count": sum(age > max_quote_age_ms for age in quote_ages),
        "strike_selection_error": strike_selection,
        "source_event_hashes": sorted(source_hashes),
        "thresholds": {"max_lateness_ms": max_lateness_ms, "max_quote_age_ms": max_quote_age_ms},
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    report["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report
