"""Append-only, in-memory event log with as-of replay (CP-0121, CP-0126, CP-0128, CP-0069, CP-0132).

Each state change is a new *version* keyed by ``(event_id, status)``. Versions are
never replaced. Re-appending an identical version is an idempotent no-op; a
different payload under the same key fails closed with :class:`ConflictingVersion`.

Visibility is decided by the time a version became knowable:
``CANDIDATE -> candidate_known_at``, ``CONFIRMED -> confirmed_at``,
``INVALIDATED -> invalidated_at``. ``as_of`` returns, per event, the newest
version known by the cutoff. EXPIRED and SUPERSEDED have no time field in the
current model, so they are rejected here rather than guessed (CP-0124 is open).
"""

from __future__ import annotations

from datetime import datetime

from financial_brain.patterns.spec import (
    EventState,
    InvalidContract,
    PatternEvent,
    check_transition,
)


class ConflictingVersion(ValueError):
    """Same ``(event_id, status)`` already stored with a different payload."""


def _aware(label: str, text: str) -> datetime:
    try:
        ts = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise InvalidContract(f"{label}: bad timestamp {text!r}") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise InvalidContract(f"{label}: timestamp must be timezone-aware")
    return ts


def version_known_at(event: PatternEvent) -> datetime:
    """The instant at which this version became knowable."""
    if event.status is EventState.CANDIDATE:
        return _aware("candidate_known_at_utc", event.candidate_known_at_utc)
    if event.status is EventState.CONFIRMED:
        assert event.confirmed_at_utc is not None  # enforced by PatternEvent
        return _aware("confirmed_at_utc", event.confirmed_at_utc)
    if event.status is EventState.INVALIDATED:
        assert event.invalidated_at_utc is not None
        return _aware("invalidated_at_utc", event.invalidated_at_utc)
    raise InvalidContract(f"{event.status.value} has no time field in the current model")


def sorted_events(events: list[PatternEvent]) -> list[PatternEvent]:
    """Canonical order, independent of input order (CP-0069, CP-0132)."""
    return sorted(
        events,
        key=lambda e: (
            _aware("candidate_known_at_utc", e.candidate_known_at_utc),
            e.lineage,
            e.timeframe,
            e.event_id,
            e.status.value,
        ),
    )


class EventLog:
    def __init__(self) -> None:
        self._versions: list[PatternEvent] = []
        self._digests: dict[tuple[str, str], str] = {}

    def __len__(self) -> int:
        return len(self._versions)

    def append(self, event: PatternEvent) -> bool:
        """Store a version. Returns False for an identical duplicate, True if stored."""
        key = (event.event_id, event.status.value)
        digest = event.payload_sha256()
        if key in self._digests:
            if self._digests[key] == digest:
                return False
            raise ConflictingVersion(f"{key[0]} {key[1]} already stored with a different payload")
        prior = self.latest(event.event_id)
        if prior is None:
            if event.status is not EventState.CANDIDATE:
                raise InvalidContract("first version of an event must be CANDIDATE")
        else:
            check_transition(prior.status, event.status)
        version_known_at(event)  # fail closed for unmodelled states before storing
        self._versions.append(event)
        self._digests[key] = digest
        return True

    def history(self, event_id: str) -> list[PatternEvent]:
        return [v for v in self._versions if v.event_id == event_id]

    def latest(self, event_id: str) -> PatternEvent | None:
        versions = self.history(event_id)
        return versions[-1] if versions else None

    def as_of(self, cutoff_utc: str) -> list[PatternEvent]:
        """Per event, the newest version knowable at ``cutoff_utc``, in canonical order."""
        cutoff = _aware("cutoff_utc", cutoff_utc)
        best: dict[str, tuple[datetime, int, PatternEvent]] = {}
        for position, version in enumerate(self._versions):
            known = version_known_at(version)
            if known > cutoff:
                continue
            current = best.get(version.event_id)
            if current is None or (known, position) > (current[0], current[1]):
                best[version.event_id] = (known, position, version)
        return sorted_events([item[2] for item in best.values()])
