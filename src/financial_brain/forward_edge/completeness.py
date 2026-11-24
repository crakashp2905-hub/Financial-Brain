"""Immutable daily session completeness/evidence eligibility records (FE-0076)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


class CompletenessError(ValueError):
    """Invalid or conflicting session completeness evidence."""


@dataclass(frozen=True)
class SessionCompleteness:
    session_id: str
    session_date: date
    generated_at: datetime
    expected_segments: tuple[str, ...]
    received_segments: tuple[str, ...]
    incidents: tuple[str, ...]
    close_report_hash: str | None
    close_report_complete: bool
    missing_segments: tuple[str, ...]
    evidence_status: str
    eligible: bool
    report_hash: str

    def body(self) -> dict[str, object]:
        return {"session_id": self.session_id, "session_date": self.session_date.isoformat(),
                "generated_at": self.generated_at.astimezone(timezone.utc).isoformat(),
                "expected_segments": list(self.expected_segments),
                "received_segments": list(self.received_segments), "incidents": list(self.incidents),
                "close_report_hash": self.close_report_hash,
                "close_report_complete": self.close_report_complete,
                "missing_segments": list(self.missing_segments), "evidence_status": self.evidence_status,
                "eligible": self.eligible, "report_hash": self.report_hash}


def build_completeness_report(*, session_id: str, session_date: date, generated_at: datetime,
                              expected_segments: tuple[str, ...], received_segments: tuple[str, ...],
                              incidents: tuple[str, ...] = (), close_report_hash: str | None = None,
                              close_report_complete: bool = False) -> SessionCompleteness:
    """Make omissions visible; never infer eligibility from an empty incident list alone."""
    if not session_id.strip():
        raise CompletenessError("session_id is required")
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise CompletenessError("generated_at must be timezone aware")
    if not expected_segments or any(not value.strip() for value in expected_segments):
        raise CompletenessError("expected segments must be explicit and non-empty")
    if len(set(expected_segments)) != len(expected_segments):
        raise CompletenessError("expected segments contain duplicates")
    if len(set(received_segments)) != len(received_segments):
        raise CompletenessError("received segments contain duplicates")
    if set(received_segments) - set(expected_segments):
        raise CompletenessError("received segment was not declared as expected")
    if any(not value.strip() for value in incidents):
        raise CompletenessError("incident identifiers cannot be blank")
    if close_report_complete and not close_report_hash:
        raise CompletenessError("complete close evidence requires its content hash")
    missing = tuple(sorted(set(expected_segments) - set(received_segments)))
    complete = not missing and close_report_complete and not incidents
    status = "ELIGIBLE" if complete else "EVIDENCE_DOWNGRADED"
    fields = {"session_id": session_id, "session_date": session_date.isoformat(),
              "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
              "expected_segments": list(expected_segments), "received_segments": list(received_segments),
              "incidents": list(incidents), "close_report_hash": close_report_hash,
              "close_report_complete": close_report_complete, "missing_segments": list(missing),
              "evidence_status": status, "eligible": complete}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return SessionCompleteness(session_id, session_date, generated_at, expected_segments, received_segments,
                               incidents, close_report_hash, close_report_complete, missing, status, complete, digest)


class CompletenessJournal:
    """Append-only session report archive; same ID is idempotent only for identical content."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS completeness_reports (session_id TEXT PRIMARY KEY, report_hash TEXT NOT NULL, report_json TEXT NOT NULL)")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, report: SessionCompleteness) -> bool:
        encoded = json.dumps(report.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT report_hash,report_json FROM completeness_reports WHERE session_id=?",
                                  [report.session_id]).fetchone()
        if prior:
            if prior != (report.report_hash, encoded):
                raise CompletenessError("session report is immutable; changed content conflicts")
            return False
        self._con.execute("INSERT INTO completeness_reports VALUES (?,?,?)",
                          [report.session_id, report.report_hash, encoded])
        self._con.commit()
        return True

    def get(self, session_id: str) -> dict[str, object] | None:
        row = self._con.execute("SELECT report_json FROM completeness_reports WHERE session_id=?", [session_id]).fetchone()
        return json.loads(row[0]) if row else None
