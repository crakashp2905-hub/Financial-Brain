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


@dataclass(frozen=True)
class CoverageReport:
    expected_sessions: int
    covered_sessions: int
    expected_names: int
    covered_names: int
    requested_intent_notional: float
    covered_intent_notional: float
    quote_coverage_fraction: float
    depth_coverage_fraction: float
    source_classes: tuple[str, ...]
    missing_keys: tuple[str, ...]

    @property
    def claim_scope(self) -> str:
        return "LIVE_OBSERVED" if self.source_classes == ("live",) else "FIXTURE_OR_MIXED"


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

    def snapshot(self, *, decision_id: str, venue: str, instrument_id: str, decision_time: datetime) -> dict[str, object]:
        """Freeze exactly those archived events available no later than the decision clock."""
        if not decision_id.strip() or decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("decision ID and timezone-aware decision_time are required")
        cutoff = decision_time.astimezone(timezone.utc).isoformat()
        rows = self._con.execute("""
            SELECT event_id, event_sha256, available_at FROM market_events
            WHERE venue=? AND instrument_id=? AND available_at<=?
            ORDER BY available_at, event_id
        """, [venue, instrument_id, cutoff]).fetchall()
        members = [{"event_id": event_id, "sha256": digest, "available_at": available}
                   for event_id, digest, available in rows]
        if not members:
            raise QuoteArchiveError("no archived events were available at decision time")
        body: dict[str, object] = {"schema": "fb.market-decision-snapshot.v1", "decision_id": decision_id,
                                  "venue": venue, "instrument_id": instrument_id,
                                  "decision_time": cutoff, "events": members}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        body["snapshot_sha256"] = __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()
        return body

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

    def coverage(self, *, expected_sessions: set[str],
                 intents: dict[tuple[str, str], float], as_of: datetime) -> CoverageReport:
        """Measure session/name/notional quote and depth coverage against explicit denominators.

        `intents` keys are `(session_id, instrument_id)` and values are requested INR notional.
        Source values equal to `kite` or `exchange` are treated as live labels; this report does
        not authenticate them, so independent evidence is still required for formal claims.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone aware")
        if not expected_sessions or any(not k[0] or not k[1] for k in intents):
            raise ValueError("coverage needs expected sessions and valid intent keys")
        if any(not isinstance(value, (int, float)) or value < 0 for value in intents.values()):
            raise ValueError("intent notionals must be finite non-negative numbers")
        by_session_names: dict[str, set[str]] = {s: set() for s in expected_sessions}
        covered_quotes: set[tuple[str, str]] = set()
        covered_depth: set[tuple[str, str]] = set()
        sources: set[str] = set()
        missing: list[str] = []
        cutoff = as_of.astimezone(timezone.utc)
        for session_id, instrument_id in intents:
            if session_id not in expected_sessions:
                raise ValueError(f"intent session {session_id} is not in expected_sessions")
            by_session_names[session_id].add(instrument_id)
            quote = self._con.execute("""
                SELECT kind, event_time, received_at, source FROM market_events
                WHERE instrument_id = ? AND available_at <= ? AND kind IN ('quote','book_depth')
                ORDER BY available_at DESC, event_id LIMIT 10
            """, [instrument_id, cutoff.isoformat()]).fetchall()
            quote_ok = any(kind == "quote" for kind, *_ in quote)
            depth_ok = any(kind == "book_depth" for kind, *_ in quote)
            key = (session_id, instrument_id)
            if quote_ok:
                covered_quotes.add(key)
            if depth_ok:
                covered_depth.add(key)
            if not quote_ok:
                missing.append(f"{session_id}:{instrument_id}:QUOTE")
            if not depth_ok:
                missing.append(f"{session_id}:{instrument_id}:DEPTH")
            sources.update("live" if source.lower() in {"kite", "exchange", "zerodha_kite"} else "fixture"
                           for _, _, _, source in quote)
        expected_name_keys = [(s, name) for s in expected_sessions
                              for session, name in intents if session == s]
        expected_notional = sum(float(v) for v in intents.values())
        quote_notional = sum(float(intents[k]) for k in covered_quotes)
        depth_notional = sum(float(intents[k]) for k in covered_depth)
        quote_fraction = quote_notional / expected_notional if expected_notional else 0.0
        depth_fraction = depth_notional / expected_notional if expected_notional else 0.0
        covered_sessions = {session for session, _ in covered_quotes}
        covered_names = {instrument for _, instrument in covered_quotes}
        name_count = len({instrument for _, instrument in expected_name_keys})
        return CoverageReport(len(expected_sessions), len(covered_sessions), name_count, len(covered_names),
                              expected_notional, quote_notional, quote_fraction, depth_fraction,
                              tuple(sorted(sources)), tuple(sorted(missing)))
