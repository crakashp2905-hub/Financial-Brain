"""Persistent fail-closed entry kill switch with scoped human reset approval (FE-0108)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .approvals import Approval, ApprovalError, validate_approval


KILL_REASONS = frozenset({"DAILY_LOSS", "DRAWDOWN", "STALE_FEED", "RECONCILIATION_MISMATCH",
                          "LOST_HEARTBEAT", "BROKER_STATE_UNKNOWN", "RISK_LIMIT_BREACH"})
GENESIS_HASH = "0" * 64
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class KillSwitchError(PermissionError):
    """Kill switch is active, corrupted or lacks a valid human reset approval."""


@dataclass(frozen=True)
class KillEvent:
    event_id: str
    action: str
    reason: str
    occurred_at: datetime
    actor_id: str
    actor_type: str
    evidence_hash: str
    approval_hash: str | None = None

    def body(self) -> dict[str, object]:
        return {"event_id": self.event_id, "action": self.action, "reason": self.reason,
                "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "actor_id": self.actor_id, "actor_type": self.actor_type,
                "evidence_hash": self.evidence_hash, "approval_hash": self.approval_hash}


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _approval_hash(approval: Approval) -> str:
    body = asdict(approval)
    body["approved_at_utc"] = approval.approved_at_utc.astimezone(timezone.utc).isoformat()
    body["expires_at_utc"] = approval.expires_at_utc.astimezone(timezone.utc).isoformat()
    return _sha(body)


class PersistentKillSwitch:
    """SQLite WAL append-only event chain; only scoped human approval can clear the latch."""

    def __init__(self, path: Path, *, switch_id: str = "paper-entry",
                 connection: sqlite3.Connection | None = None) -> None:
        if not switch_id.strip():
            raise KillSwitchError("switch_id is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.switch_id = switch_id
        self._write_lock = _path_lock(self.path)
        self._owns_connection = connection is None
        self._con = connection or sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS kill_events (seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, prev_hash TEXT NOT NULL, event_json TEXT NOT NULL, event_hash TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS kill_events_no_update BEFORE UPDATE ON kill_events BEGIN SELECT RAISE(ABORT,'kill events are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS kill_events_no_delete BEFORE DELETE ON kill_events BEGIN SELECT RAISE(ABORT,'kill events are immutable'); END")
        self._con.commit()
        self._state()

    def close(self) -> None:
        if self._owns_connection:
            self._con.close()

    def _replay(self) -> tuple[list[str], str, int]:
        rows = self._con.execute("SELECT seq,prev_hash,event_json,event_hash FROM kill_events ORDER BY seq").fetchall()
        reasons: list[str] = []
        previous = GENESIS_HASH
        for expected_seq, (seq, prev_hash, encoded, digest) in enumerate(rows, start=1):
            if seq != expected_seq or prev_hash != previous:
                raise KillSwitchError("kill-switch event chain sequence or predecessor mismatch")
            body = json.loads(encoded)
            actual = _sha({"prev_hash": prev_hash, "event_json": encoded})
            if actual != digest:
                raise KillSwitchError("kill-switch event hash mismatch")
            action, reason = body["action"], body["reason"]
            if action == "TRIP":
                if reason not in KILL_REASONS:
                    raise KillSwitchError("unknown persisted kill reason")
                if reason not in reasons:
                    reasons.append(reason)
            elif action == "RESET":
                reasons.clear()
            else:
                raise KillSwitchError("unknown persisted kill action")
            previous = digest
        return reasons, previous, len(rows)

    def _state(self) -> tuple[list[str], str, int]:
        return self._replay()

    @property
    def active(self) -> bool:
        return bool(self._state()[0])

    @property
    def active_reasons(self) -> tuple[str, ...]:
        return tuple(self._state()[0])

    def _append(self, event: KillEvent) -> bool:
        # Serialize same-process users and reserve the SQLite writer slot before
        # reading the chain head. Otherwise two concurrent trips can both derive
        # the same next sequence/predecessor from a stale snapshot.
        with self._write_lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                prior = self._con.execute(
                    "SELECT event_json FROM kill_events WHERE event_id=?", [event.event_id]).fetchone()
                encoded = json.dumps(event.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
                if prior:
                    if prior[0] != encoded:
                        raise KillSwitchError("event ID reused with changed kill-switch content")
                    self._con.commit()
                    return False
                _, previous, sequence = self._state()
                digest = _sha({"prev_hash": previous, "event_json": encoded})
                self._con.execute("INSERT INTO kill_events VALUES (?,?,?,?,?)",
                                  [sequence + 1, event.event_id, previous, encoded, digest])
                self._con.commit()
                return True
            except Exception:
                self._con.rollback()
                raise

    def trip(self, *, event_id: str, reason: str, occurred_at: datetime,
             evidence_hash: str, actor_id: str = "risk-engine", actor_type: str = "system") -> bool:
        if reason not in KILL_REASONS:
            raise KillSwitchError("unknown kill reason")
        if not event_id.strip() or not evidence_hash.strip() or not actor_id.strip():
            raise KillSwitchError("event ID, evidence hash and actor are required")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise KillSwitchError("kill event time must be timezone aware")
        return self._append(KillEvent(event_id, "TRIP", reason, occurred_at, actor_id,
                                      actor_type, evidence_hash))

    def reset_scope_hash(self) -> str:
        reasons, head, _ = self._state()
        if not reasons:
            raise KillSwitchError("kill switch is not active")
        return _sha({"switch_id": self.switch_id, "active_reasons": sorted(reasons), "head_hash": head})

    def reset(self, *, event_id: str, approval: Approval, now: datetime,
              evidence_hash: str) -> bool:
        scope = self.reset_scope_hash()
        try:
            validate_approval(approval, kind="DISARM_RESET", subject=self.switch_id,
                              scope_sha256=scope, now=now)
        except ApprovalError as exc:
            raise KillSwitchError(f"human reset approval rejected: {exc}") from exc
        if not event_id.strip() or not evidence_hash.strip():
            raise KillSwitchError("reset event ID and evidence hash are required")
        return self._append(KillEvent(event_id, "RESET", "HUMAN_APPROVED_RESET", now,
                                      approval.actor_id, approval.actor_type, evidence_hash,
                                      _approval_hash(approval)))
