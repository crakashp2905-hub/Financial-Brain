"""Durable operational modes for fail-closed forward paper sessions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum


class OperatingModeError(ValueError):
    """Invalid or conflicting operational mode transition."""


class OperatingMode(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DEGRADED = "DEGRADED"
    RECOVERING = "RECOVERING"
    HALTED = "HALTED"


TRANSITIONS = {
    OperatingMode.ACTIVE: {OperatingMode.PAUSED, OperatingMode.DEGRADED, OperatingMode.HALTED},
    OperatingMode.PAUSED: {OperatingMode.ACTIVE, OperatingMode.DEGRADED, OperatingMode.HALTED},
    OperatingMode.DEGRADED: {OperatingMode.RECOVERING, OperatingMode.PAUSED, OperatingMode.HALTED},
    OperatingMode.RECOVERING: {OperatingMode.ACTIVE, OperatingMode.DEGRADED, OperatingMode.PAUSED,
                               OperatingMode.HALTED},
    OperatingMode.HALTED: set(),
}


class OperatingModeLedger:
    """Append-only mode history and current projection sharing the runner SQLite database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS operating_mode_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            event_id TEXT NOT NULL, mode TEXT NOT NULL, occurred_at TEXT NOT NULL,
            reason TEXT NOT NULL, prior_sha256 TEXT, event_sha256 TEXT NOT NULL,
            UNIQUE(session_id,event_id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS operating_modes (
            session_id TEXT PRIMARY KEY, mode TEXT NOT NULL, event_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL, reason TEXT NOT NULL, event_sha256 TEXT NOT NULL)""")
        connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS operating_mode_events_no_update
            BEFORE UPDATE ON operating_mode_events
            BEGIN SELECT RAISE(ABORT, 'operating mode history is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS operating_mode_events_no_delete
            BEFORE DELETE ON operating_mode_events
            BEGIN SELECT RAISE(ABORT, 'operating mode history is append-only'); END;
        """)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise OperatingModeError("operating mode timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    def initialize(self, session_id: str, *, occurred_at: datetime) -> bool:
        if not session_id.strip():
            raise OperatingModeError("session ID is required")
        if self.connection.execute("SELECT 1 FROM operating_modes WHERE session_id=?", [session_id]).fetchone():
            return False
        return self.transition(session_id, "ACTIVE", event_id=f"{session_id}:mode:active",
                               occurred_at=occurred_at, reason="session operating mode initialized",
                               allow_initial=True)

    def mode(self, session_id: str) -> OperatingMode:
        row = self.connection.execute("SELECT mode FROM operating_modes WHERE session_id=?", [session_id]).fetchone()
        if row is None:
            raise OperatingModeError("operating mode is not initialized for this session")
        return OperatingMode(row[0])

    def transition(self, session_id: str, mode: str | OperatingMode, *, event_id: str,
                   occurred_at: datetime, reason: str, allow_initial: bool = False) -> bool:
        try:
            target = OperatingMode(mode)
        except ValueError as exc:
            raise OperatingModeError("unsupported operating mode") from exc
        stamp = self._utc(occurred_at)
        if not session_id.strip() or not event_id.strip() or not reason.strip():
            raise OperatingModeError("session, event ID and transition reason are required")
        existing = self.connection.execute(
            "SELECT mode,occurred_at,reason FROM operating_mode_events WHERE session_id=? AND event_id=?",
            [session_id, event_id]).fetchone()
        if existing:
            if existing != (target.value, stamp.isoformat(), reason):
                raise OperatingModeError("operating mode event ID reused with conflicting content")
            return False
        prior = self.connection.execute(
            "SELECT mode,occurred_at,event_sha256 FROM operating_modes WHERE session_id=?", [session_id]).fetchone()
        if prior is None and not (allow_initial and target is OperatingMode.ACTIVE):
            raise OperatingModeError("operating mode must be initialized before transition")
        if prior is not None:
            prior_mode = OperatingMode(prior[0])
            prior_at = self._utc(datetime.fromisoformat(prior[1]))
            if stamp < prior_at:
                raise OperatingModeError("operating mode clock cannot move backwards")
            if target not in TRANSITIONS[prior_mode]:
                raise OperatingModeError(f"invalid operating mode transition: {prior_mode.value} -> {target.value}")
        prior_hash = prior[2] if prior else None
        body = {"session_id": session_id, "event_id": event_id, "mode": target.value,
                "occurred_at": stamp.isoformat(), "reason": reason, "prior_sha256": prior_hash}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False).encode("utf-8")).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            latest = self.connection.execute(
                "SELECT mode,occurred_at,event_sha256 FROM operating_modes WHERE session_id=?", [session_id]
            ).fetchone()
            if latest != prior:
                raise OperatingModeError("operating mode changed concurrently; retry with a new event ID")
            self.connection.execute(
                "INSERT INTO operating_mode_events(session_id,event_id,mode,occurred_at,reason,prior_sha256,event_sha256) "
                "VALUES(?,?,?,?,?,?,?)", [session_id, event_id, target.value, stamp.isoformat(), reason, prior_hash, digest])
            self.connection.execute(
                "INSERT INTO operating_modes VALUES(?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "mode=excluded.mode,event_id=excluded.event_id,occurred_at=excluded.occurred_at,"
                "reason=excluded.reason,event_sha256=excluded.event_sha256",
                [session_id, target.value, event_id, stamp.isoformat(), reason, digest])
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def events(self, session_id: str) -> list[dict[str, str | None]]:
        return [{"event_id": row[0], "mode": row[1], "occurred_at": row[2], "reason": row[3],
                 "prior_sha256": row[4], "event_sha256": row[5]}
                for row in self.connection.execute(
                    "SELECT event_id,mode,occurred_at,reason,prior_sha256,event_sha256 "
                    "FROM operating_mode_events WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()]


__all__ = ["OperatingMode", "OperatingModeError", "OperatingModeLedger"]
