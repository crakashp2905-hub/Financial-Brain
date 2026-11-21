"""Closing logic gaps in six partial items (FB-0044, FB-0050, FB-0053, FB-0054, FB-0055, FB-0058).

* ``MarketCalendar`` gives trading days from weekdays minus a holiday set, and the next session after a date.
* ``ASSET_CLASS_REQUIRED`` lists the fields each asset class needs; ``missing_fields`` reports the gaps. Unknown classes
  are refused.
* ``DataStatus`` and ``status_of`` classify a feed as LIVE, STALE or DOWN from its age.
* ``causal_violations`` lists events whose parent does not appear earlier in the sequence.
* ``DedupeLedger`` accepts a key once; a repeat is reported, not reapplied.
* ``FrozenSnapshot`` is an immutable record of positions and cash with a stable digest.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

ASSET_CLASS_REQUIRED: dict[str, frozenset[str]] = {
    "EQUITY": frozenset({"lot"}),
    "FUTURE": frozenset({"expiry", "multiplier"}),
    "OPTION": frozenset({"strike", "expiry"}),
    "FX": frozenset({"pair"}),
}


@dataclass(frozen=True)
class MarketCalendar:
    holidays: frozenset[date] = frozenset()

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def next_session(self, day: date) -> date:
        probe = day + timedelta(days=1)
        for _ in range(366):
            if self.is_session(probe):
                return probe
            probe += timedelta(days=1)
        raise RuntimeError("no session found within a year")


def missing_fields(asset_class: str, attributes: dict[str, object]) -> list[str]:
    if asset_class not in ASSET_CLASS_REQUIRED:
        raise KeyError(f"unknown asset class {asset_class!r}")
    return sorted(ASSET_CLASS_REQUIRED[asset_class] - set(attributes))


class DataStatus(Enum):
    LIVE = "LIVE"
    STALE = "STALE"
    DOWN = "DOWN"


def status_of(age_s: float, stale_after_s: float, down_after_s: float) -> DataStatus:
    if stale_after_s >= down_after_s:
        raise ValueError("stale threshold must be below the down threshold")
    if age_s > down_after_s:
        return DataStatus.DOWN
    if age_s > stale_after_s:
        return DataStatus.STALE
    return DataStatus.LIVE


def causal_violations(events: Sequence[tuple[str, str | None]]) -> list[str]:
    seen: set[str] = set()
    bad: list[str] = []
    for event_id, parent in events:
        if parent is not None and parent not in seen:
            bad.append(event_id)
        seen.add(event_id)
    return bad


@dataclass
class DedupeLedger:
    _seen: set[str] = field(default_factory=set)

    def add(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


@dataclass(frozen=True)
class FrozenSnapshot:
    positions: tuple[tuple[str, int], ...]
    cash: str

    @property
    def digest(self) -> str:
        body = repr((self.positions, self.cash))
        return hashlib.sha256(body.encode()).hexdigest()
