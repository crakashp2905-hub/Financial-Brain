"""Deterministic event kernel (FB-0165, FB-0167, FB-0168, FB-0172, FB-0173, FB-0174, FB-0177).

* Events are ordered by (event_time, sequence) so a replay produces the same journal byte for byte.
* A watermark advances with event time. Events older than ``watermark - lateness`` are quarantined as LATE; they
  never rewrite decisions that were already emitted.
* The queue is bounded: a full queue raises ``Backpressure`` instead of growing memory.
* ``checkpoint`` / ``resume`` replay from an offset; an accepted side effect is recorded by key and is never
  dispatched twice, even after a crash-and-resume.
* ``snapshot_checksum`` makes any change to a persisted journal detectable.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from financial_brain.kernel.clock import utc


class Backpressure(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Event:
    event_time: datetime
    sequence: int
    kind: str = field(compare=False)
    payload: tuple[tuple[str, Any], ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", utc(self.event_time))
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")


class EventKernel:
    def __init__(self, *, max_queue: int = 10_000, lateness: timedelta = timedelta(seconds=5)) -> None:
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        self._max = max_queue
        self._lateness = lateness
        self._queue: list[Event] = []
        self.watermark: datetime | None = None
        self.journal: list[Event] = []
        self.late: list[Event] = []
        self._dispatched_keys: set[str] = set()
        self.side_effects: list[str] = []

    def submit(self, event: Event) -> None:
        if len(self._queue) >= self._max:
            raise Backpressure(f"queue full at {self._max} events")
        self._queue.append(event)

    def drain(self, handler: Callable[[Event], None] | None = None) -> int:
        """Process queued events in deterministic (event_time, sequence) order. Late events are quarantined."""
        processed = 0
        for event in sorted(self._queue):
            if self.watermark is not None and event.event_time < self.watermark - self._lateness:
                self.late.append(event)
                continue
            if self.watermark is None or event.event_time > self.watermark:
                self.watermark = event.event_time
            self.journal.append(event)
            if handler is not None:
                handler(event)
            processed += 1
        self._queue.clear()
        return processed

    def side_effect(self, key: str, action: Callable[[], None]) -> bool:
        """Run an external-facing action at most once per key. Returns False if it was already dispatched."""
        if key in self._dispatched_keys:
            return False
        self._dispatched_keys.add(key)
        self.side_effects.append(key)
        action()
        return True

    def checkpoint(self) -> dict[str, Any]:
        return {
            "offset": len(self.journal),
            "watermark": self.watermark.isoformat() if self.watermark else None,
            "dispatched": sorted(self._dispatched_keys),
        }

    def resume(self, checkpoint: dict[str, Any], replay: Iterable[Event]) -> None:
        """Rebuild state from a checkpoint by skipping journalled offsets; no event is journalled twice."""
        self.journal = []
        self.watermark = datetime.fromisoformat(checkpoint["watermark"]) if checkpoint["watermark"] else None
        self._dispatched_keys = set(checkpoint["dispatched"])
        skip = int(checkpoint["offset"])
        for i, event in enumerate(sorted(replay)):
            if i < skip:
                self.journal.append(event)
                continue
            self._queue.append(event)
        self.drain()


def snapshot_checksum(events: Iterable[Event]) -> str:
    rows = [
        {"t": e.event_time.isoformat(), "seq": e.sequence, "kind": e.kind, "payload": list(e.payload)}
        for e in events
    ]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()
