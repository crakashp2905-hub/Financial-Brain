"""Append-only stop-research registry for failed strategy families."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


GENESIS = "0" * 64


class FamilyRegistryError(PermissionError):
    """A family is closed, a reopening basis is inadequate, or the registry is corrupt."""


class ReopenBasisKind(StrEnum):
    NEW_INFORMATION = "NEW_INFORMATION"
    NEW_PREREGISTERED_MECHANISM = "NEW_PREREGISTERED_MECHANISM"


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FamilyRegistryError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise FamilyRegistryError(f"{name} must be lowercase SHA-256")


@dataclass(frozen=True)
class ReopenBasis:
    kind: ReopenBasisKind
    description: str
    source_sha256: str
    registered_at: datetime
    became_available_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReopenBasisKind) or not self.description.strip():
            raise FamilyRegistryError("reopen basis type and description are required")
        _sha(self.source_sha256, "reopen basis source")
        registered = _utc(self.registered_at, "basis registration timestamp")
        available = _utc(self.became_available_at, "basis availability timestamp")
        if available > registered and self.kind is ReopenBasisKind.NEW_PREREGISTERED_MECHANISM:
            raise FamilyRegistryError("a mechanism must be preregistered before outcome access, not after its result")


class FamilyResearchRegistry:
    """Persistent hash-chained event registry; local hashes are tamper-evident, not independent custody."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS family_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            family_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS active_family_attempts (
            family_id TEXT PRIMARY KEY,
            attempt_id TEXT UNIQUE NOT NULL)""")
        # Recover active registrations written before the transactional guard was added.
        families = self._con.execute("SELECT DISTINCT family_id FROM family_events").fetchall()
        for (family_id,) in families:
            latest = self._con.execute(
                "SELECT event_type,body_json FROM family_events WHERE family_id=? ORDER BY sequence DESC LIMIT 1",
                (family_id,),
            ).fetchone()
            if latest and latest[0] == "ATTEMPT_REGISTERED":
                body = json.loads(latest[1])
                self._con.execute("INSERT OR IGNORE INTO active_family_attempts VALUES (?,?)",
                                  (family_id, body["attempt_id"]))

    def close(self) -> None:
        self._con.close()

    def _events(self, family_id: str) -> list[tuple]:
        return self._con.execute("SELECT event_id,event_type,body_json FROM family_events "
                                 "WHERE family_id=? ORDER BY sequence", (family_id,)).fetchall()

    def state(self, family_id: str) -> str:
        rows = self._events(family_id)
        if not rows:
            return "UNREGISTERED"
        event_type = rows[-1][1]
        if event_type == "ATTEMPT_REGISTERED":
            return "ATTEMPT_ACTIVE"
        body = json.loads(rows[-1][2])
        return "CLOSED_FAILED" if body.get("verdict") in {"FAIL", "NO_EDGE"} else "CLOSED_REVIEWED"

    def _append(self, event_id: str, family_id: str, event_type: str, body: dict) -> str:
        previous_row = self._con.execute("SELECT event_hash FROM family_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = previous_row[0] if previous_row else GENESIS
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256((previous + canonical).encode()).hexdigest()
        try:
            self._con.execute("INSERT INTO family_events "
                              "(event_id,family_id,event_type,body_json,previous_hash,event_hash) VALUES (?,?,?,?,?,?)",
                              (event_id, family_id, event_type, canonical, previous, digest))
        except sqlite3.IntegrityError as exc:
            raise FamilyRegistryError("event IDs must be unique") from exc
        return digest

    def register_attempt(self, *, family_id: str, attempt_id: str, preregistration_sha256: str,
                         registered_at: datetime, first_outcome_access_at: datetime,
                         basis: ReopenBasis | None = None) -> str:
        if not family_id.strip() or not attempt_id.strip():
            raise FamilyRegistryError("family and attempt IDs are required")
        _sha(preregistration_sha256, "preregistration")
        registered = _utc(registered_at, "attempt registration timestamp")
        access = _utc(first_outcome_access_at, "first outcome access timestamp")
        if registered >= access:
            raise FamilyRegistryError("attempt must be preregistered before its outcome is accessed")
        self._con.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(family_id)
            if events and self.state(family_id) == "ATTEMPT_ACTIVE":
                raise FamilyRegistryError("family already has an active registered attempt")
            if events:
                if basis is None:
                    raise FamilyRegistryError("closed family requires new information or a preregistered mechanism")
                basis_registered = _utc(basis.registered_at, "basis registration timestamp")
                available = _utc(basis.became_available_at, "basis availability timestamp")
                last_result = next((json.loads(row[2]) for row in reversed(events)
                                    if row[1] == "RESULT_RECORDED"), None)
                if last_result is None:
                    raise FamilyRegistryError("reopening basis requires a prior recorded family verdict")
                closed_at = datetime.fromisoformat(last_result["recorded_at_utc"])
                if available <= _utc(closed_at, "prior verdict timestamp"):
                    raise FamilyRegistryError("reopening basis must be new since the prior family verdict")
                if basis_registered >= access:
                    raise FamilyRegistryError("reopening basis must be preregistered before outcome access")
                if basis.kind is ReopenBasisKind.NEW_PREREGISTERED_MECHANISM and basis_registered <= _utc(
                        closed_at, "prior verdict timestamp"):
                    raise FamilyRegistryError("new mechanism must be registered after the prior failure")
            try:
                self._con.execute("INSERT INTO active_family_attempts (family_id,attempt_id) VALUES (?,?)",
                                  (family_id, attempt_id))
            except sqlite3.IntegrityError as exc:
                raise FamilyRegistryError("family already has an active registered attempt") from exc
            body = {"family_id": family_id, "attempt_id": attempt_id,
                    "preregistration_sha256": preregistration_sha256,
                    "registered_at_utc": registered.isoformat(), "first_outcome_access_at_utc": access.isoformat(),
                    "basis": None if basis is None else {
                        "kind": basis.kind.value, "description": basis.description,
                        "source_sha256": basis.source_sha256,
                        "registered_at_utc": _utc(basis.registered_at, "basis registration timestamp").isoformat(),
                        "became_available_at_utc": _utc(basis.became_available_at, "basis availability timestamp").isoformat(),
                    }}
            digest = self._append(f"attempt:{attempt_id}", family_id, "ATTEMPT_REGISTERED", body)
            self._con.execute("COMMIT")
            return digest
        except Exception:
            self._con.execute("ROLLBACK")
            raise

    def record_result(self, *, family_id: str, attempt_id: str, verdict: str,
                      result_sha256: str, recorded_at: datetime) -> str:
        if verdict not in {"FAIL", "NO_EDGE", "INCONCLUSIVE", "POSITIVE_REVIEW_REQUIRED"}:
            raise FamilyRegistryError("unsupported terminal research verdict")
        _sha(result_sha256, "result")
        at = _utc(recorded_at, "result timestamp")
        self._con.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(family_id)
            active = self._con.execute(
                "SELECT attempt_id FROM active_family_attempts WHERE family_id=?", (family_id,)
            ).fetchone()
            if not events or events[-1][1] != "ATTEMPT_REGISTERED" or active is None:
                raise FamilyRegistryError("a registered active attempt is required before recording its result")
            attempt = json.loads(events[-1][2])
            if attempt["attempt_id"] != attempt_id or active[0] != attempt_id:
                raise FamilyRegistryError("result attempt ID differs from the active registration")
            if at < datetime.fromisoformat(attempt["first_outcome_access_at_utc"]):
                raise FamilyRegistryError("result cannot precede first outcome access")
            digest = self._append(f"result:{attempt_id}", family_id, "RESULT_RECORDED",
                                  {"family_id": family_id, "attempt_id": attempt_id, "verdict": verdict,
                                   "result_sha256": result_sha256, "recorded_at_utc": at.isoformat()})
            self._con.execute("DELETE FROM active_family_attempts WHERE family_id=? AND attempt_id=?",
                              (family_id, attempt_id))
            self._con.execute("COMMIT")
            return digest
        except Exception:
            self._con.execute("ROLLBACK")
            raise

    def audit(self) -> dict[str, object]:
        rows = self._con.execute("SELECT event_id,family_id,event_type,body_json,previous_hash,event_hash "
                                 "FROM family_events ORDER BY sequence").fetchall()
        previous = GENESIS
        for event_id, family_id, event_type, body, prior, digest in rows:
            if prior != previous or hashlib.sha256((prior + body).encode()).hexdigest() != digest:
                raise FamilyRegistryError("family registry hash chain is corrupt")
            parsed = json.loads(body)
            if parsed.get("family_id") != family_id:
                raise FamilyRegistryError("family registry event identity mismatch")
            if event_type == "ATTEMPT_REGISTERED" and event_id != f"attempt:{parsed.get('attempt_id')}":
                raise FamilyRegistryError("family attempt event identity mismatch")
            previous = digest
        return {"events": len(rows), "families": len({row[1] for row in rows}),
                "head_sha256": previous, "integrity": "VALID"}


__all__ = ["FamilyRegistryError", "FamilyResearchRegistry", "ReopenBasis", "ReopenBasisKind"]
