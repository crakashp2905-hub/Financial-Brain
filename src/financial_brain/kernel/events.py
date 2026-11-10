"""Deterministic event kernel (FB-0165, FB-0167, FB-0168, FB-0172, FB-0173, FB-0174, FB-0177).

* Events are ordered by (event_time, sequence) so a replay produces the same journal byte for byte.
* A watermark advances with event time. Events older than ``watermark - lateness`` are quarantined as LATE; they
  never rewrite decisions that were already emitted.
* The queue is bounded: a full queue raises ``Backpressure`` instead of growing memory.
* ``checkpoint`` / ``resume`` replay from an offset; an accepted side effect is recorded by key and is never
  dispatched twice, even after a crash-and-resume.
* ``snapshot_checksum`` makes any change to a persisted journal detectable.

Side-effect outbox semantics (FE-0011, FE-0013):

* A key is marked DISPATCHED only **after** its action returns. Marking it before the action (the earlier
  behaviour) turned an injected exception into a false acknowledgement.
* If the action raises, the key becomes UNKNOWN: whether the external effect happened is not known. An UNKNOWN key
  is never re-run automatically; ``side_effect`` raises ``UnknownOutcome`` until ``resolve_unknown`` records a
  reconciliation (the effect happened, or it provably did not).

The outbox is in-memory only. Durable write-ahead persistence (FE-0012) is not implemented here.
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


class UnknownOutcome(RuntimeError):
    """An external action raised, or was left unresolved; it must be reconciled before any replay."""


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
        self._unknown_keys: set[str] = set()
        self.side_effects: list[str] = []
        self.reconciliations: list[tuple[str, bool, str]] = []

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
        """Run an external-facing action at most once per key.

        Returns False if the key was already dispatched (nothing runs). Raises ``UnknownOutcome`` if the key is
        unresolved; the action is not re-run. If ``action`` raises, the key is marked UNKNOWN and the error is
        raised as ``UnknownOutcome`` - it is never recorded as dispatched.
        """
        if key in self._dispatched_keys:
            return False
        if key in self._unknown_keys:
            raise UnknownOutcome(f"{key}: outcome unknown; reconcile before replay")
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - any failure leaves the external effect undetermined
            self._unknown_keys.add(key)
            raise UnknownOutcome(f"{key}: action failed, outcome unknown") from exc
        self._dispatched_keys.add(key)
        self.side_effects.append(key)
        return True

    def resolve_unknown(self, key: str, *, took_effect: bool, reconciled_by: str) -> None:
        """Record a reconciliation of an UNKNOWN key. ``took_effect=True`` closes it without a re-run."""
        if key not in self._unknown_keys:
            raise UnknownOutcome(f"{key}: not in UNKNOWN state")
        if not reconciled_by or not reconciled_by.strip():
            raise UnknownOutcome("reconciliation needs a named reconciler")
        self._unknown_keys.discard(key)
        self.reconciliations.append((key, bool(took_effect), reconciled_by))
        if took_effect:
            self._dispatched_keys.add(key)
            self.side_effects.append(key)

    @property
    def unknown_keys(self) -> frozenset[str]:
        return frozenset(self._unknown_keys)

    def checkpoint(self) -> dict[str, Any]:
        return {
            "offset": len(self.journal),
            "watermark": self.watermark.isoformat() if self.watermark else None,
            "dispatched": sorted(self._dispatched_keys),
            "unknown": sorted(self._unknown_keys),
        }

    def resume(self, checkpoint: dict[str, Any], replay: Iterable[Event]) -> None:
        """Rebuild state from a checkpoint by skipping journalled offsets; no event is journalled twice."""
        self.journal = []
        self.watermark = datetime.fromisoformat(checkpoint["watermark"]) if checkpoint["watermark"] else None
        self._dispatched_keys = set(checkpoint["dispatched"])
        self._unknown_keys = set(checkpoint.get("unknown", []))
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
