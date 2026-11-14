"""Fail-closed quote/depth coverage gate for sub-30-minute alpha studies.

Bar-only observations are useful for signal research, but cannot establish executable
fill economics. This module requires a fresh, observable two-sided top of book for every
predeclared candidate interval and never imputes a spread or drops uncovered intervals.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Sequence

from financial_brain.forward_edge.market_data import (
    NormalizedEvent,
    executable_quote,
    quote_quality_reasons,
)


class CoverageInputError(ValueError):
    """Invalid coverage request; invalid inputs must not be treated as eligible."""


@dataclass(frozen=True)
class RequiredInterval:
    interval_id: str
    instrument: str
    execution_utc: datetime


@dataclass(frozen=True)
class IntervalEvidence:
    interval_id: str
    instrument: str
    quote_sha256: str | None
    spread_bps: float | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutableCoverageReport:
    status: str
    required_intervals: int
    covered_intervals: int
    coverage_pct: float
    allow_executable_alpha: bool
    interval_evidence: tuple[IntervalEvidence, ...]
    evidence_sha256: str


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CoverageInputError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def assess_sub30_executable_coverage(
    required_intervals: Sequence[RequiredInterval],
    quotes: Sequence[NormalizedEvent],
    *,
    interval_seconds: int,
    max_quote_age_s: float = 5.0,
    max_spread_bps: float = 50.0,
) -> ExecutableCoverageReport:
    """Gate an executable-alpha study on complete top-of-book observations.

    A valid quote must be available by the interval's execution time, have explicit source
    and exchange provenance, and pass the existing two-sided price, displayed-size, age,
    spread, auction, halt, and circuit checks. Any uncovered candidate interval blocks the
    entire study; it is never silently removed from the denominator.
    """
    if type(interval_seconds) is not int or not 1 <= interval_seconds < 30 * 60:
        raise CoverageInputError("interval_seconds must be an integer below 30 minutes")
    if type(max_quote_age_s) not in {int, float} or not isfinite(max_quote_age_s) or max_quote_age_s < 0:
        raise CoverageInputError("max_quote_age_s must be finite and non-negative")
    if type(max_spread_bps) not in {int, float} or not isfinite(max_spread_bps) or max_spread_bps < 0:
        raise CoverageInputError("max_spread_bps must be finite and non-negative")

    intervals = tuple(required_intervals)
    if not intervals:
        raise CoverageInputError("at least one predeclared candidate interval is required")
    ids = [item.interval_id for item in intervals]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise CoverageInputError("interval IDs must be non-empty and unique")
    normalized_times = {
        item.interval_id: _utc(item.execution_utc, "execution_utc") for item in intervals
    }

    per_interval: list[IntervalEvidence] = []
    for required in intervals:
        when = normalized_times[required.interval_id]
        candidates = [
            event for event in quotes
            if event.kind == "QUOTE" and event.instrument == required.instrument
            and _utc(event.available_utc, "quote available_utc") <= when
        ]
        candidates.sort(key=lambda event: _utc(event.available_utc, "quote available_utc"), reverse=True)
        reasons: tuple[str, ...] = ("MISSING_QUOTE",)
        selected: NormalizedEvent | None = None
        spread_bps: float | None = None
        for event in candidates:
            payload = dict(event.payload)
            observed_reasons = set(quote_quality_reasons(
                event, max_age_s=float(max_quote_age_s), max_spread_bps=float(max_spread_bps)
            ))
            if _utc(event.event_utc, "quote event_utc") > when:
                observed_reasons.add("FUTURE_EVENT")
            elif (when - _utc(event.event_utc, "quote event_utc")).total_seconds() > max_quote_age_s:
                observed_reasons.add("STALE_AT_EXECUTION")
            if not payload.get("source"):
                observed_reasons.add("MISSING_SOURCE_PROVENANCE")
            if not payload.get("exchange"):
                observed_reasons.add("MISSING_EXCHANGE_PROVENANCE")
            executable, executable_reason = executable_quote(event)
            if not executable:
                observed_reasons.add(executable_reason)
            if not observed_reasons:
                from decimal import Decimal

                bid, ask = Decimal(payload["bid_price"]), Decimal(payload["ask_price"])
                spread_bps = float((ask - bid) / ((ask + bid) / Decimal(2)) * Decimal(10_000))
                selected = event
                reasons = ()
            else:
                reasons = tuple(sorted(observed_reasons))
            # Use the freshest quote available at the execution timestamp. Falling back
            # to an older valid quote after a newer unusable update would misstate the
            # market seen by the simulator.
            break

        per_interval.append(IntervalEvidence(
            interval_id=required.interval_id,
            instrument=required.instrument,
            quote_sha256=selected.content_sha256() if selected else None,
            spread_bps=spread_bps,
            reasons=reasons,
        ))

    covered = sum(item.quote_sha256 is not None for item in per_interval)
    coverage_pct = round(100.0 * covered / len(intervals), 6)
    eligible = covered == len(intervals)
    evidence_obj = {
        "interval_seconds": interval_seconds,
        "max_quote_age_s": max_quote_age_s,
        "max_spread_bps": max_spread_bps,
        "intervals": [
            {
                "interval_id": item.interval_id,
                "instrument": item.instrument,
                "execution_utc": normalized_times[item.interval_id].isoformat(),
            }
            for item in intervals
        ],
        "evidence": [
            {
                "interval_id": item.interval_id,
                "quote_sha256": item.quote_sha256,
                "spread_bps": item.spread_bps,
                "reasons": list(item.reasons),
            }
            for item in per_interval
        ],
    }
    digest = hashlib.sha256(json.dumps(
        evidence_obj, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return ExecutableCoverageReport(
        status="ELIGIBLE_FOR_EXECUTABLE_ALPHA_TEST" if eligible else "BLOCKED_INSUFFICIENT_ORDER_BOOK_COVERAGE",
        required_intervals=len(intervals),
        covered_intervals=covered,
        coverage_pct=coverage_pct,
        allow_executable_alpha=eligible,
        interval_evidence=tuple(per_interval),
        evidence_sha256=digest,
    )
