"""Data validation, quarantine, freshness and bitemporal lookup (FB-0066, FB-0067, FB-0069, FB-0070, FB-0072).

* ``validate_bar`` checks OHLC bounds, non-negative volume and finiteness. Failing bars are quarantined with reason
  codes; they never enter the clean series.
* Duplicates follow one deterministic policy per source: identical rows collapse, conflicting rows for the same key
  are both quarantined (no silent last-write-wins).
* ``Revisions`` keep every revision with its publication time. ``as_of`` answers "what did we know at time t" and
  never returns a revision published after t.
* ``is_fresh`` blocks use of data older than its SLA.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from financial_brain.kernel.clock import utc


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def validate_bar(bar: Bar) -> list[str]:
    reasons: list[str] = []
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return ["NON_FINITE"]
    if bar.volume < 0:
        reasons.append("NEGATIVE_VOLUME")
    if bar.low > bar.high:
        reasons.append("LOW_ABOVE_HIGH")
    if not (bar.low <= bar.open <= bar.high):
        reasons.append("OPEN_OUTSIDE_RANGE")
    if not (bar.low <= bar.close <= bar.high):
        reasons.append("CLOSE_OUTSIDE_RANGE")
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        reasons.append("NON_POSITIVE_PRICE")
    return reasons


@dataclass
class CleanSeries:
    clean: list[Bar] = field(default_factory=list)
    quarantine: list[tuple[Bar, list[str]]] = field(default_factory=list)


def clean_series(bars: list[Bar]) -> CleanSeries:
    """Validate, de-duplicate and sort. Same key + identical values collapses; conflicting values are quarantined."""
    out = CleanSeries()
    seen: dict[datetime, Bar] = {}
    conflicted: set[datetime] = set()
    for bar in bars:
        reasons = validate_bar(bar)
        if reasons:
            out.quarantine.append((bar, reasons))
            continue
        ts = utc(bar.ts)
        prior = seen.get(ts)
        if prior is None:
            seen[ts] = bar
        elif prior != bar:
            conflicted.add(ts)
    for ts in sorted(conflicted):
        out.quarantine.extend(
            (b, ["CONFLICTING_DUPLICATE"]) for b in bars if utc(b.ts) == ts and not validate_bar(b)
        )
        seen.pop(ts, None)
    out.clean = [seen[t] for t in sorted(seen)]
    return out


@dataclass(frozen=True)
class Revision:
    key: str
    value: Any
    published_at: datetime
    observed_at: datetime


class Revisions:
    """Append-only, bitemporal store. Corrections add revisions; nothing is overwritten."""

    def __init__(self) -> None:
        self._rows: list[Revision] = []

    def add(self, rev: Revision) -> None:
        if utc(rev.observed_at) > utc(rev.published_at):
            raise ValueError("an observation cannot be published before it was observed")
        self._rows.append(
            Revision(rev.key, rev.value, utc(rev.published_at), utc(rev.observed_at))
        )

    def as_of(self, key: str, when: datetime) -> Revision | None:
        t = utc(when)
        candidates = [r for r in self._rows if r.key == key and r.published_at <= t]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.published_at, r.observed_at))


def is_fresh(last_update: datetime, now: datetime, sla: timedelta) -> bool:
    """Data older than its SLA must block dependent workflows (FB-0069)."""
    age = utc(now) - utc(last_update)
    return timedelta(0) <= age <= sla
