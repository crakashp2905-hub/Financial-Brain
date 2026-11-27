"""Durable bounded retry schedule for failed forward-session event handlers."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


class RecoveryLedgerError(ValueError):
    """Invalid recovery attempt, retry schedule, or conflicting handler identity."""


def retry_delay(attempt: int, *, base: timedelta, cap: timedelta) -> timedelta:
    if type(attempt) is not int or attempt < 1 or base < timedelta(0) or cap < timedelta(0):
        raise RecoveryLedgerError("attempt and retry durations must be non-negative and valid")
    if base > cap:
        raise RecoveryLedgerError("retry base cannot exceed retry cap")
    return min(cap, base * (2 ** min(attempt - 1, 100)))


class SessionRecoveryLedger:
    """Append-only handler failures plus a durable attempt and retry-time projection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS handler_recovery_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            consumer_id TEXT NOT NULL, event_id TEXT NOT NULL, handler TEXT NOT NULL,
            attempt INTEGER NOT NULL, occurred_at TEXT NOT NULL, retry_at TEXT,
            status TEXT NOT NULL, error_type TEXT NOT NULL, error_message TEXT NOT NULL,
            prior_sha256 TEXT, event_sha256 TEXT NOT NULL,
            UNIQUE(session_id,consumer_id,event_id,handler,attempt,status))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS handler_recovery_state (
            session_id TEXT NOT NULL, consumer_id TEXT NOT NULL, event_id TEXT NOT NULL,
            handler TEXT NOT NULL, attempt INTEGER NOT NULL, retry_at TEXT,
            status TEXT NOT NULL, event_sha256 TEXT NOT NULL,
            PRIMARY KEY(session_id,consumer_id,event_id,handler))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS handler_recovery_alerts (
            root_cause_sha256 TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            consumer_id TEXT NOT NULL, event_id TEXT NOT NULL, handler TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, error_type TEXT NOT NULL, error_message TEXT NOT NULL)""")
        connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS handler_recovery_events_no_update
            BEFORE UPDATE ON handler_recovery_events
            BEGIN SELECT RAISE(ABORT, 'handler recovery events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS handler_recovery_events_no_delete
            BEFORE DELETE ON handler_recovery_events
            BEGIN SELECT RAISE(ABORT, 'handler recovery events are append-only'); END;
        """)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise RecoveryLedgerError("recovery timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    def state(self, session_id: str, consumer_id: str, event_id: str, handler: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT attempt,retry_at,status,event_sha256 FROM handler_recovery_state "
            "WHERE session_id=? AND consumer_id=? AND event_id=? AND handler=?",
            [session_id, consumer_id, event_id, handler]).fetchone()
        if row is None:
            return None
        return {"attempt": int(row[0]), "retry_at": row[1], "status": row[2], "event_sha256": row[3]}

    def record_failure(self, *, session_id: str, consumer_id: str, event_id: str, handler: str,
                       occurred_at: datetime, error: Exception, max_attempts: int,
                       base_delay: timedelta, max_delay: timedelta) -> dict[str, Any]:
        stamp = self._utc(occurred_at)
        if not all(value.strip() for value in (session_id, consumer_id, event_id, handler)):
            raise RecoveryLedgerError("session, consumer, event and handler IDs are required")
        if type(max_attempts) is not int or max_attempts < 1:
            raise RecoveryLedgerError("max_attempts must be a positive integer")
        previous = self.state(session_id, consumer_id, event_id, handler)
        if previous and previous["status"] in {"RECOVERED", "HALTED"}:
            return previous
        attempt = (previous["attempt"] if previous else 0) + 1
        status = "HALTED" if attempt >= max_attempts else "RETRY_WAIT"
        delay = retry_delay(attempt, base=base_delay, cap=max_delay)
        retry_at = None if status == "HALTED" else (stamp + delay).isoformat()
        error_type, error_message = type(error).__name__, str(error)[:500]
        prior_hash = previous["event_sha256"] if previous else None
        body = {"session_id": session_id, "consumer_id": consumer_id, "event_id": event_id,
                "handler": handler, "attempt": attempt, "occurred_at": stamp.isoformat(),
                "retry_at": retry_at, "status": status, "error_type": error_type,
                "error_message": error_message, "prior_sha256": prior_hash}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")).hexdigest()
        root = hashlib.sha256(json.dumps({"session_id": session_id, "consumer_id": consumer_id,
                                         "event_id": event_id, "handler": handler,
                                         "error_type": error_type, "error_message": error_message},
                                        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            latest = self.state(session_id, consumer_id, event_id, handler)
            if latest != previous:
                raise RecoveryLedgerError("recovery attempt changed concurrently; retry with a fresh observation")
            self.connection.execute(
                "INSERT INTO handler_recovery_events(session_id,consumer_id,event_id,handler,attempt,occurred_at,"
                "retry_at,status,error_type,error_message,prior_sha256,event_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [session_id, consumer_id, event_id, handler, attempt, stamp.isoformat(), retry_at,
                 status, error_type, error_message, prior_hash, digest])
            self.connection.execute(
                "INSERT INTO handler_recovery_state VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id,consumer_id,event_id,handler) DO UPDATE SET "
                "attempt=excluded.attempt,retry_at=excluded.retry_at,status=excluded.status,"
                "event_sha256=excluded.event_sha256",
                [session_id, consumer_id, event_id, handler, attempt, retry_at, status, digest])
            alert_insert = self.connection.execute(
                "INSERT OR IGNORE INTO handler_recovery_alerts VALUES(?,?,?,?,?,?,?,?)",
                [root, session_id, consumer_id, event_id, handler, stamp.isoformat(), error_type, error_message])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"attempt": attempt, "retry_at": retry_at, "status": status,
                "event_sha256": digest, "root_cause_sha256": root,
                "alert_created": alert_insert.rowcount == 1}

    def record_success(self, *, session_id: str, consumer_id: str, event_id: str,
                       handler: str, occurred_at: datetime) -> None:
        previous = self.state(session_id, consumer_id, event_id, handler)
        if previous is None or previous["status"] == "RECOVERED":
            return
        if previous["status"] == "HALTED":
            raise RecoveryLedgerError("a halted handler cannot be marked recovered")
        stamp = self._utc(occurred_at)
        body = {"session_id": session_id, "consumer_id": consumer_id, "event_id": event_id,
                "handler": handler, "attempt": previous["attempt"], "occurred_at": stamp.isoformat(),
                "retry_at": None, "status": "RECOVERED", "error_type": "", "error_message": "",
                "prior_sha256": previous["event_sha256"]}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            latest = self.state(session_id, consumer_id, event_id, handler)
            if latest != previous:
                raise RecoveryLedgerError("recovery state changed concurrently; retry with a fresh observation")
            self.connection.execute(
                "INSERT INTO handler_recovery_events(session_id,consumer_id,event_id,handler,attempt,occurred_at,"
                "retry_at,status,error_type,error_message,prior_sha256,event_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [session_id, consumer_id, event_id, handler, previous["attempt"], stamp.isoformat(),
                 None, "RECOVERED", "", "", previous["event_sha256"], digest])
            self.connection.execute(
                "UPDATE handler_recovery_state SET retry_at=NULL,status='RECOVERED',event_sha256=? "
                "WHERE session_id=? AND consumer_id=? AND event_id=? AND handler=?",
                [digest, session_id, consumer_id, event_id, handler])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def events(self, session_id: str) -> list[dict[str, Any]]:
        return [{"consumer_id": row[0], "event_id": row[1], "handler": row[2],
                 "attempt": row[3], "occurred_at": row[4], "retry_at": row[5],
                 "status": row[6], "error_type": row[7], "error_message": row[8],
                 "event_sha256": row[9]}
                for row in self.connection.execute(
                    "SELECT consumer_id,event_id,handler,attempt,occurred_at,retry_at,status,error_type,"
                    "error_message,event_sha256 FROM handler_recovery_events "
                    "WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()]


__all__ = ["RecoveryLedgerError", "SessionRecoveryLedger", "retry_delay"]
