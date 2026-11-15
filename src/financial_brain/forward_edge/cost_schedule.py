"""Immutable effective-dated cost schedules using the existing India cost model (FE-0060)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from financial_brain.costs.india import CostModel, Segment, Side


class CostScheduleError(ValueError):
    """Missing or conflicting cost schedule evidence."""


@dataclass(frozen=True)
class CostSchedule:
    schedule_id: str
    version: str
    effective_from: date
    effective_to: date | None
    segment: Segment
    model: CostModel
    source_ref: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.schedule_id, self.version, self.source_ref)):
            raise CostScheduleError("schedule ID, version and source reference are required")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise CostScheduleError("effective_to precedes effective_from")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise CostScheduleError("source_sha256 must be lowercase SHA-256")

    def digest(self) -> str:
        payload = {"schedule_id": self.schedule_id, "version": self.version,
                   "effective_from": self.effective_from.isoformat(),
                   "effective_to": self.effective_to.isoformat() if self.effective_to else None,
                   "segment": self.segment.value, "model": self.model.as_dict(),
                   "source_ref": self.source_ref, "source_sha256": self.source_sha256}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode("utf-8")).hexdigest()

    def one_way(self, *, day: date, turnover: float, side: Side, bucket: str) -> dict[str, Any]:
        if day < self.effective_from or (self.effective_to is not None and day > self.effective_to):
            raise CostScheduleError("schedule is not effective on requested trade date")
        row = self.model.one_way(turnover=turnover, side=side, segment=self.segment, bucket=bucket)
        return {**row, "schedule_id": self.schedule_id, "schedule_version": self.version,
                "schedule_sha256": self.digest(), "trade_date": day.isoformat(),
                "source_ref": self.source_ref, "source_sha256": self.source_sha256}


class CostScheduleBook:
    def __init__(self, schedules: tuple[CostSchedule, ...] = ()) -> None:
        self._schedules: dict[tuple[str, str], CostSchedule] = {}
        for schedule in schedules:
            self.register(schedule)

    def register(self, schedule: CostSchedule) -> str:
        key = (schedule.schedule_id, schedule.version)
        old = self._schedules.get(key)
        if old is not None:
            if old.digest() != schedule.digest():
                raise CostScheduleError("schedule identity is immutable; change the version")
            return old.digest()
        for prior in self._schedules.values():
            if prior.schedule_id == schedule.schedule_id and prior.segment == schedule.segment:
                old_end = prior.effective_to or date.max
                new_end = schedule.effective_to or date.max
                if max(prior.effective_from, schedule.effective_from) <= min(old_end, new_end):
                    raise CostScheduleError("effective ranges overlap for the same schedule and segment")
        self._schedules[key] = schedule
        return schedule.digest()

    def resolve(self, *, schedule_id: str, segment: Segment, day: date) -> CostSchedule:
        matches = [s for s in self._schedules.values() if s.schedule_id == schedule_id and s.segment == segment
                   and day >= s.effective_from and (s.effective_to is None or day <= s.effective_to)]
        if len(matches) != 1:
            raise CostScheduleError("no unique effective cost schedule for date and segment")
        return matches[0]
