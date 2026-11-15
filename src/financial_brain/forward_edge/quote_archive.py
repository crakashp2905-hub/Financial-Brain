"""Append-only normalized market-event archive and feed health diagnostics.

The archive stores observed-event claims plus their provenance; fixture rows remain
fixtures and cannot be mistaken for vendor or exchange observations.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.market_events import MarketEvent


class QuoteArchiveError(ValueError):
    """Archive rejected a duplicate, conflicting or invalid event."""


@dataclass(frozen=True)
class FeedHealth:
    received: int
    sequence_gaps: int
    late_events: int
    max_transport_lag_ms: float
    last_received_at: datetime | None


class QuoteArchive:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS market_events (
                event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, venue TEXT NOT NULL,
                instrument_id TEXT NOT NULL, event_time TEXT NOT NULL, received_at TEXT NOT NULL,
                available_at TEXT NOT NULL, sequence INTEGER, source TEXT NOT NULL,
                evidence_id TEXT, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL
            )
        """)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, event: MarketEvent) -> bool:
        body = event.as_dict()
        digest = event.sha256()
        previous = self._con.execute("SELECT event_sha256 FROM market_events WHERE event_id=?",
                                     [event.event_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise QuoteArchiveError("event_id reused with different content")
            return False
        self._con.execute("INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          [event.event_id, event.kind.value, event.venue, event.instrument_id,
                           event.event_time.isoformat(), event.received_at.isoformat(),
                           event.available_at.isoformat(), event.sequence, event.source,
                           event.evidence_id, digest, json.dumps(body, sort_keys=True, separators=(",", ":"))])
        self._con.commit()
        return True

    def get(self, event_id: str) -> MarketEvent:
        row = self._con.execute("SELECT event_json FROM market_events WHERE event_id=?", [event_id]).fetchone()
        if row is None:
            raise KeyError(event_id)
        return MarketEvent.from_dict(json.loads(row[0]))

    def health(self, *, venue: str, instrument_id: str, as_of: datetime | None = None) -> FeedHealth:
        # `as_of` is retained for a stable report contract; rows are not filtered
        # because the archive health summary measures observed history, not a cutoff.
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of must be timezone aware")
        rows = self._con.execute("""
            SELECT event_time, received_at, sequence FROM market_events
            WHERE venue=? AND instrument_id=? ORDER BY received_at, event_id
        """, [venue, instrument_id]).fetchall()
        sequences = [int(seq) for _, _, seq in rows if seq is not None]
        gaps = sum(max(0, later - earlier - 1) for earlier, later in zip(sequences, sequences[1:]))
        lags = [(datetime.fromisoformat(received) - datetime.fromisoformat(event)).total_seconds() * 1000
                for event, received, _ in rows]
        late = sum(datetime.fromisoformat(event) > datetime.fromisoformat(received) for event, received, _ in rows)
        last = max((datetime.fromisoformat(r[1]) for r in rows), default=None)
        if last is not None:
            last = last.astimezone(timezone.utc)
        return FeedHealth(len(rows), gaps, late, max(lags, default=0.0), last)

    def count(self) -> int:
        return int(self._con.execute("SELECT count(*) FROM market_events").fetchone()[0])

    def rows(self) -> list[dict[str, object]]:
        names = [x[1] for x in self._con.execute("PRAGMA table_info(market_events)").fetchall()]
        return [dict(zip(names, row, strict=True)) for row in self._con.execute(
            "SELECT * FROM market_events ORDER BY received_at, event_id").fetchall()]
