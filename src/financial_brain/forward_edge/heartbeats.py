"""Durable, component-scoped session heartbeats with last-good event evidence.

This is an observability gate, not proof that an external process is healthy. A component
must publish its own heartbeat; absent or stale producers keep the session degraded.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


COMPONENTS = (
    "quote_feed", "completed_bars", "strategy_process", "clock_sync",
    "ledger_writer", "position_monitor",
)


class HeartbeatError(ValueError):
    """Invalid, regressing or conflicting component heartbeat."""


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HeartbeatError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


class SessionHeartbeatLedger:
    """Append-only heartbeat events plus a per-session latest-state projection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        connection.execute("""CREATE TABLE IF NOT EXISTS session_heartbeat_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            component TEXT NOT NULL, heartbeat_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL, last_good_event_id TEXT, last_good_event_at TEXT,
            prior_sha256 TEXT, event_sha256 TEXT NOT NULL,
            UNIQUE(session_id, component, heartbeat_id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS session_heartbeats (
            session_id TEXT NOT NULL, component TEXT NOT NULL, heartbeat_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL, last_good_event_id TEXT, last_good_event_at TEXT,
            event_sha256 TEXT NOT NULL, PRIMARY KEY(session_id, component))""")
        connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS heartbeat_events_no_update
            BEFORE UPDATE ON session_heartbeat_events
            BEGIN SELECT RAISE(ABORT, 'heartbeat event log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS heartbeat_events_no_delete
            BEFORE DELETE ON session_heartbeat_events
            BEGIN SELECT RAISE(ABORT, 'heartbeat event log is append-only'); END;
        """)

    def record(self, *, session_id: str, component: str, heartbeat_id: str,
               occurred_at: datetime, last_good_event_id: str | None = None,
               last_good_event_at: datetime | None = None) -> bool:
        if not session_id.strip() or component not in COMPONENTS or not heartbeat_id.strip():
            raise HeartbeatError("session, supported component and heartbeat ID are required")
        occurred = _utc(occurred_at, "heartbeat time")
        if (last_good_event_id is None) != (last_good_event_at is None):
            raise HeartbeatError("last-good event ID and clock must be supplied together")
        good_at = _utc(last_good_event_at, "last-good event time") if last_good_event_at else None
        if good_at is not None and good_at > occurred:
            raise HeartbeatError("last-good event cannot follow its heartbeat")
        duplicate = self.connection.execute(
            "SELECT occurred_at,last_good_event_id,last_good_event_at FROM session_heartbeat_events "
            "WHERE session_id=? AND component=? AND heartbeat_id=?",
            [session_id, component, heartbeat_id]).fetchone()
        expected_duplicate = (occurred.isoformat(), last_good_event_id,
                              good_at.isoformat() if good_at else None)
        if duplicate:
            if duplicate != expected_duplicate:
                raise HeartbeatError("heartbeat ID reused with conflicting content")
            return False
        previous = self.connection.execute(
            "SELECT heartbeat_id, occurred_at, last_good_event_id, last_good_event_at, event_sha256 "
            "FROM session_heartbeats WHERE session_id=? AND component=?",
            [session_id, component]).fetchone()
        if previous:
            previous_at = datetime.fromisoformat(previous[1])
            if occurred < previous_at:
                raise HeartbeatError("component heartbeat clock cannot move backwards")
        prior_hash = previous[4] if previous else None
        body = {"session_id": session_id, "component": component, "heartbeat_id": heartbeat_id,
                "occurred_at": occurred.isoformat(), "last_good_event_id": last_good_event_id,
                "last_good_event_at": good_at.isoformat() if good_at else None,
                "prior_sha256": prior_hash}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            # Recheck under the write lock so parallel producers cannot fork a component chain.
            latest = self.connection.execute(
                "SELECT heartbeat_id, occurred_at, last_good_event_id, last_good_event_at, event_sha256 "
                "FROM session_heartbeats WHERE session_id=? AND component=?",
                [session_id, component]).fetchone()
            if latest != previous:
                raise HeartbeatError("component heartbeat changed concurrently; retry with a new heartbeat")
            self.connection.execute("INSERT INTO session_heartbeat_events "
                                    "(session_id,component,heartbeat_id,occurred_at,last_good_event_id,"
                                    "last_good_event_at,prior_sha256,event_sha256) VALUES (?,?,?,?,?,?,?,?)",
                                    [session_id, component, heartbeat_id, occurred.isoformat(),
                                     last_good_event_id, good_at.isoformat() if good_at else None,
                                     prior_hash, digest])
            prior_good = datetime.fromisoformat(previous[3]) if previous and previous[3] else None
            if prior_good is not None and (good_at is None or good_at < prior_good):
                projected_good_id, projected_good_at = previous[2], previous[3]
            else:
                projected_good_id = last_good_event_id
                projected_good_at = good_at.isoformat() if good_at else None
            self.connection.execute("INSERT INTO session_heartbeats VALUES (?,?,?,?,?,?,?) "
                                    "ON CONFLICT(session_id,component) DO UPDATE SET "
                                    "heartbeat_id=excluded.heartbeat_id, occurred_at=excluded.occurred_at, "
                                    "last_good_event_id=excluded.last_good_event_id, "
                                    "last_good_event_at=excluded.last_good_event_at, "
                                    "event_sha256=excluded.event_sha256",
                                    [session_id, component, heartbeat_id, occurred.isoformat(),
                                     projected_good_id, projected_good_at, digest])
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def last_heartbeat_at(self, session_id: str, component: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT occurred_at FROM session_heartbeats WHERE session_id=? AND component=?",
            [session_id, component]).fetchone()
        return datetime.fromisoformat(row[0]).astimezone(timezone.utc) if row else None

    def snapshot(self, session_id: str, *, now: datetime, stale_after: timedelta) -> dict[str, Any]:
        observed = _utc(now, "snapshot time")
        if not isinstance(stale_after, timedelta) or stale_after <= timedelta(0):
            raise HeartbeatError("stale_after must be a positive duration")
        latest = {str(row[0]): row[1:] for row in self.connection.execute(
            "SELECT component,heartbeat_id,occurred_at,last_good_event_id,last_good_event_at,event_sha256 "
            "FROM session_heartbeats WHERE session_id=?", [session_id]).fetchall()}
        components: dict[str, dict[str, Any]] = {}
        for component in COMPONENTS:
            row = latest.get(component)
            if row is None:
                components[component] = {"status": "NEVER_SEEN", "last_heartbeat_at": None,
                                         "last_good_event_id": None, "last_good_event_at": None,
                                         "heartbeat_age_ms": None, "event_sha256": None}
                continue
            heartbeat_at = datetime.fromisoformat(row[1]).astimezone(timezone.utc)
            age = observed - heartbeat_at
            if age < timedelta(0):
                raise HeartbeatError(f"snapshot clock precedes {component} heartbeat")
            components[component] = {
                "status": "HEALTHY" if age <= stale_after else "STALE",
                "last_heartbeat_at": heartbeat_at.isoformat(),
                "last_good_event_id": row[2], "last_good_event_at": row[3],
                "heartbeat_age_ms": age.total_seconds() * 1000,
                "event_sha256": row[4],
            }
        ready = all(row["status"] == "HEALTHY" for row in components.values())
        return {"schema": "fb.session_component_heartbeats.v1", "session_id": session_id,
                "status": "HEALTHY" if ready else "DEGRADED", "entry_readiness": "READY" if ready else "NO_ENTRY",
                "components": components,
                "missing_or_stale": [name for name, row in components.items()
                                     if row["status"] != "HEALTHY"]}

    def latest(self, session_id: str) -> dict[str, Any]:
        """Return deterministic persisted state for reproducible session evidence records."""
        rows = {str(row[0]): row[1:] for row in self.connection.execute(
            "SELECT component,heartbeat_id,occurred_at,last_good_event_id,last_good_event_at,event_sha256 "
            "FROM session_heartbeats WHERE session_id=?", [session_id]).fetchall()}
        components = {}
        for component in COMPONENTS:
            row = rows.get(component)
            components[component] = ({"status": "RECORDED", "heartbeat_id": row[0],
                                      "last_heartbeat_at": row[1], "last_good_event_id": row[2],
                                      "last_good_event_at": row[3], "event_sha256": row[4]}
                                     if row else
                                     {"status": "NEVER_SEEN", "heartbeat_id": None,
                                      "last_heartbeat_at": None, "last_good_event_id": None,
                                      "last_good_event_at": None, "event_sha256": None})
        return {"schema": "fb.session_component_heartbeats.v1", "session_id": session_id,
                "components": components}


__all__ = ["COMPONENTS", "HeartbeatError", "SessionHeartbeatLedger"]
