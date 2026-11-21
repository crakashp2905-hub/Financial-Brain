"""Evidence-linked full-session timeline with explicit zero-signal session denominators (FE-0119)."""
from __future__ import annotations

import hashlib
import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionTimelineError(ValueError):
    """Timeline event conflicts with session, clock or evidence requirements."""


EVENT_KINDS = frozenset({"PREFLIGHT", "SIGNAL", "NO_TRADE", "FILL", "MISSED_FILL",
                         "INCIDENT", "SESSION_CLOSE"})


@dataclass(frozen=True)
class TimelineEvent:
    session_id: str
    event_id: str
    kind: str
    occurred_at: datetime
    source_id: str
    source_sha256: str
    details: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.session_id, self.event_id, self.source_id, self.details)):
            raise SessionTimelineError("timeline identity, source and details are required")
        if self.kind not in EVENT_KINDS:
            raise SessionTimelineError("unknown timeline event kind")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise SessionTimelineError("timeline clock must be timezone aware")
        if len(self.source_sha256) != 64 or any(
                character not in "0123456789abcdef" for character in self.source_sha256):
            raise SessionTimelineError("timeline source must carry lowercase SHA-256")

    def body(self) -> dict[str, str]:
        return {"session_id": self.session_id, "event_id": self.event_id, "kind": self.kind,
                "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "source_id": self.source_id, "source_sha256": self.source_sha256,
                "details": self.details}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class SessionTimelineRow:
    session_id: str
    events: tuple[TimelineEvent, ...]
    missing_categories: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class SessionTimelineReport:
    expected_session_ids: tuple[str, ...]
    rows: tuple[SessionTimelineRow, ...]
    report_hash: str


def build_session_timeline(*, expected_session_ids: tuple[str, ...],
                           events: tuple[TimelineEvent, ...], as_of: datetime) -> SessionTimelineReport:
    """Preserve every expected exchange session, including an explicit empty/omitted row."""
    if not expected_session_ids or any(not item.strip() for item in expected_session_ids):
        raise SessionTimelineError("expected session denominator must be explicit and non-empty")
    if len(set(expected_session_ids)) != len(expected_session_ids):
        raise SessionTimelineError("expected session IDs must be unique")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise SessionTimelineError("timeline as_of must be timezone aware")
    expected = set(expected_session_ids)
    if any(event.session_id not in expected for event in events):
        raise SessionTimelineError("event belongs to a session outside the expected denominator")
    if any(event.occurred_at > as_of for event in events):
        raise SessionTimelineError("future timeline events are not permitted")
    event_ids = [event.event_id for event in events]
    if len(set(event_ids)) != len(event_ids):
        raise SessionTimelineError("event IDs must be unique in the timeline report")

    grouped: dict[str, list[TimelineEvent]] = {session_id: [] for session_id in expected_session_ids}
    for event in events:
        grouped[event.session_id].append(event)
    rows = []
    for session_id in expected_session_ids:
        ordered = tuple(sorted(grouped[session_id],
                               key=lambda event: (event.occurred_at.astimezone(timezone.utc),
                                                  event.event_id)))
        present = {event.kind for event in ordered}
        missing: list[str] = []
        if "PREFLIGHT" not in present:
            missing.append("PREFLIGHT")
        if not present.intersection({"SIGNAL", "NO_TRADE"}):
            missing.append("DECISION")
        if "SESSION_CLOSE" not in present:
            missing.append("SESSION_CLOSE")
        rows.append(SessionTimelineRow(session_id, ordered, tuple(missing), not missing))

    payload: dict[str, Any] = {"expected_session_ids": list(expected_session_ids),
                               "as_of": as_of.astimezone(timezone.utc).isoformat(),
                               "rows": [{"session_id": row.session_id,
                                         "events": [event.body() for event in row.events],
                                         "missing_categories": list(row.missing_categories),
                                         "complete": row.complete} for row in rows]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    return SessionTimelineReport(expected_session_ids, tuple(rows), digest)


class SessionTimelineJournal:
    """Append-only SQLite event archive for replayable per-session timelines."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS timeline_events (event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, event_hash TEXT NOT NULL, event_json TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS timeline_events_no_update BEFORE UPDATE ON timeline_events BEGIN SELECT RAISE(ABORT,'timeline events are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS timeline_events_no_delete BEFORE DELETE ON timeline_events BEGIN SELECT RAISE(ABORT,'timeline events are immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, event: TimelineEvent) -> bool:
        encoded = json.dumps(event.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT event_hash,event_json FROM timeline_events WHERE event_id=?",
                                  [event.event_id]).fetchone()
        if prior:
            if prior != (event.digest(), encoded):
                raise SessionTimelineError("timeline event ID reused with changed content")
            return False
        self._con.execute("INSERT INTO timeline_events VALUES (?,?,?,?)",
                          [event.event_id, event.session_id, event.digest(), encoded])
        self._con.commit()
        return True

    def events(self, session_id: str) -> tuple[TimelineEvent, ...]:
        records = self._con.execute("SELECT event_json FROM timeline_events WHERE session_id=?",
                                    [session_id]).fetchall()
        values = []
        for (encoded,) in records:
            row = json.loads(encoded)
            values.append(TimelineEvent(row["session_id"], row["event_id"], row["kind"],
                                        datetime_from_iso(row["occurred_at"]), row["source_id"],
                                        row["source_sha256"], row["details"]))
        return tuple(values)


def datetime_from_iso(value: str) -> datetime:
    """Parse the journal's canonical timezone-aware ISO timestamp."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionTimelineError("journal timestamp is missing its timezone")
    return parsed


def render_session_timeline(report: SessionTimelineReport, output: Path) -> Path:
    """Render all expected sessions and their timeline gaps as escaped HTML."""
    sessions = []
    for row in report.rows:
        events = "".join("<li><b>" + html.escape(event.kind) + "</b> · "
                         + html.escape(event.occurred_at.isoformat()) + " · "
                         + html.escape(event.details) + " · source "
                         + html.escape(event.source_id) + " (" + html.escape(event.source_sha256)
                         + ")</li>" for event in row.events)
        gaps = ", ".join(row.missing_categories) or "None"
        if not events:
            events = "<li>No events recorded for this expected session</li>"
        sessions.append("<section><h2>" + html.escape(row.session_id) + " — "
                        + ("COMPLETE" if row.complete else "INCOMPLETE") + "</h2><p>Missing: "
                        + html.escape(gaps) + "</p><ol>" + events + "</ol></section>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Paper session timeline</title>"
            "<style>body{font:14px system-ui;max-width:950px;margin:2rem auto;padding:0 1rem;color:#202630}"
            "section{border:1px solid #ddd;border-radius:6px;padding:.8rem;margin:1rem 0}li{margin:.5rem 0}"
            "code{overflow-wrap:anywhere}</style><main><h1>Full-session evidence timeline</h1><p>Expected sessions: "
            + str(len(report.expected_session_ids)) + " · Completed evidence sessions: "
            + str(sum(row.complete for row in report.rows)) + " · Report SHA-256: <code>"
            + report.report_hash + "</code></p>" + "".join(sessions) + "</main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
