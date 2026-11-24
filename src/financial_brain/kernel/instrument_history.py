"""Append-only identity and corporate-action transitions for point-in-time instruments."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum


class InstrumentHistoryError(ValueError):
    """Identity transition is malformed, conflicting, or not yet publicly knowable."""


class IdentityEventKind(StrEnum):
    TICKER_CHANGE = "TICKER_CHANGE"
    ISIN_CHANGE = "ISIN_CHANGE"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    SCHEME_RENAME = "SCHEME_RENAME"
    SCHEME_MERGER = "SCHEME_MERGER"


_DESTINATION_REQUIRED = frozenset({
    IdentityEventKind.TICKER_CHANGE,
    IdentityEventKind.ISIN_CHANGE,
    IdentityEventKind.SCHEME_RENAME,
    IdentityEventKind.SCHEME_MERGER,
})


@dataclass(frozen=True)
class InstrumentIdentityEvent:
    """A sourced transition. The record links identities but never rewrites old signals."""

    event_id: str
    kind: IdentityEventKind
    from_id: str
    to_ids: tuple[str, ...]
    effective_at: date
    published_at: datetime
    recorded_at: datetime
    source_ref: str
    source_sha256: str
    details: str = ""

    def __post_init__(self) -> None:
        kind = IdentityEventKind(self.kind)
        if not all(isinstance(value, str) and value.strip() for value in
                   (self.event_id, self.from_id, self.source_ref)):
            raise InstrumentHistoryError("event id, source identity and source reference are required")
        destinations = tuple(value.strip() for value in self.to_ids)
        if any(not value for value in destinations) or len(destinations) != len(set(destinations)):
            raise InstrumentHistoryError("destination identities must be unique and non-empty")
        if kind in _DESTINATION_REQUIRED and not destinations:
            raise InstrumentHistoryError(f"{kind.value} requires at least one destination identity")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise InstrumentHistoryError("source_sha256 must be lowercase SHA-256")
        for name in ("published_at", "recorded_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise InstrumentHistoryError(f"{name} must be timezone aware")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.recorded_at < self.published_at:
            raise InstrumentHistoryError("identity event cannot be recorded before publication")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "from_id", self.from_id.strip())
        object.__setattr__(self, "to_ids", destinations)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "from_id": self.from_id,
            "to_ids": list(self.to_ids),
            "effective_at": self.effective_at.isoformat(),
            "published_at": self.published_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "details": self.details,
        }

    def sha256(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class InstrumentHistory:
    """Immutable-by-event-ID history queried with publication-time visibility."""

    def __init__(self, events: tuple[InstrumentIdentityEvent, ...] = ()) -> None:
        self._events: dict[str, InstrumentIdentityEvent] = {}
        for event in events:
            self.append(event)

    def append(self, event: InstrumentIdentityEvent) -> None:
        prior = self._events.get(event.event_id)
        if prior is not None:
            if prior != event:
                raise InstrumentHistoryError("event ID reused with conflicting identity history")
            return
        self._events[event.event_id] = event

    def known_for(self, instrument_id: str, *, as_of: datetime) -> tuple[InstrumentIdentityEvent, ...]:
        """Return transitions publicly known by ``as_of``; never mutate historical signal IDs."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise InstrumentHistoryError("as_of must be timezone aware")
        cutoff = as_of.astimezone(timezone.utc)
        return tuple(sorted(
            (event for event in self._events.values()
             if instrument_id == event.from_id or instrument_id in event.to_ids
             if event.published_at <= cutoff),
            key=lambda event: (event.effective_at, event.published_at, event.event_id),
        ))

    def announced_successors(self, instrument_id: str, *, as_of: datetime) -> tuple[str, ...]:
        events = self.known_for(instrument_id, as_of=as_of)
        return tuple(dict.fromkeys(
            destination for event in events if event.from_id == instrument_id
            for destination in event.to_ids
        ))

    def events(self) -> tuple[InstrumentIdentityEvent, ...]:
        return tuple(sorted(self._events.values(), key=lambda event: event.event_id))


__all__ = [
    "IdentityEventKind", "InstrumentHistory", "InstrumentHistoryError",
    "InstrumentIdentityEvent",
]
