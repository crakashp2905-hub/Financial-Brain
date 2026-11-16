"""Atomic append-only durability primitives shared by the forward-session runner."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class DurableJournalError(ValueError):
    """Durable journal rejected conflicting or malformed evidence."""


class DurableSessionJournal:
    """Append lifecycle events and their audit records in the runner's SQLite database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute("""CREATE TABLE IF NOT EXISTS runner_audit_log (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            record_id TEXT NOT NULL, kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
            record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
            UNIQUE(session_id, record_id))""")
        self.connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS runner_audit_no_update
            BEFORE UPDATE ON runner_audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS runner_audit_no_delete
            BEFORE DELETE ON runner_audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
        """)

    @staticmethod
    def canonical(record: dict[str, Any]) -> tuple[str, str]:
        try:
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DurableJournalError(f"record is not canonical JSON: {exc}") from exc
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def append_audit(self, *, session_id: str, kind: str, record_id: str,
                     occurred_at: datetime, record: dict[str, Any]) -> bool:
        if not session_id.strip() or not kind.strip() or not record_id.strip():
            raise DurableJournalError("audit session, kind and record identity are required")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise DurableJournalError("audit timestamp must be timezone aware")
        canonical, digest = self.canonical(record)
        stable_id = f"{kind}:{record_id}"
        previous = self.connection.execute(
            "SELECT record_sha256 FROM runner_audit_log WHERE session_id=? AND record_id=?",
            [session_id, stable_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise DurableJournalError("append-only audit record conflicts with prior evidence")
            return False
        self.connection.execute(
            "INSERT INTO runner_audit_log "
            "(session_id, record_id, kind, occurred_at, record_sha256, record_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [session_id, stable_id, kind, occurred_at.astimezone(timezone.utc).isoformat(), digest, canonical])
        return True

    def append_lifecycle_record(self, *, session_id: str, kind: str, event_id: str,
                                occurred_at: datetime, record: dict[str, Any]) -> bool:
        """Atomically persist the materialized lifecycle event and its immutable audit entry."""
        if not session_id.strip() or not kind.strip() or not event_id.strip():
            raise DurableJournalError("lifecycle session, kind and event identity are required")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise DurableJournalError("lifecycle timestamp must be timezone aware")
        canonical, digest = self.canonical(record)
        event_key = f"{session_id}:{kind}"
        event_time = occurred_at.astimezone(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            prior = self.connection.execute(
                "SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
                [session_id, event_key]).fetchone()
            if prior and prior[0] != digest:
                raise DurableJournalError("session lifecycle record conflicts with prior evidence")
            if not prior:
                self.connection.execute(
                    "INSERT INTO runner_events (session_id, event_id, kind, available_at, ingested_at, "
                    "ingest_clock_source, event_sha256, event_json) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)",
                    [session_id, event_key, kind, event_time, digest, canonical])
            self.append_audit(session_id=session_id, kind=kind, record_id=event_key,
                              occurred_at=occurred_at, record=record)
            self.connection.commit()
            return prior is None
        except Exception:
            self.connection.rollback()
            raise

    def audit_records(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT sequence, record_id, kind, occurred_at, record_sha256, record_json "
            "FROM runner_audit_log WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()
        return [{"kind": row[2], "record_id": row[1], "occurred_at": row[3],
                 "record_sha256": row[4], "record": json.loads(row[5])} for row in rows]


__all__ = ["DurableJournalError", "DurableSessionJournal"]
