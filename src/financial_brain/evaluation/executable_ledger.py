"""Durably freeze executable selections before outcome access and score them once.

This local SQLite ledger prevents a caller from editing the selected-signal cohort after
seeing outcomes. It records the caller-declared outcome availability time; it does not
independently attest clocks or market-data custody.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, cast

from .executable import ExecutableOutcome, resolve_executable

_GENESIS = "0" * 64


class ExecutableLedgerError(ValueError):
    """Invalid, tampered, late, or repeated executable scoring operation."""


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutableLedgerError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ExecutableSelectionLedger:
    """Hash-chained, one-score ledger for frozen executable pick cohorts."""

    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS executable_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE)""")
        self._db.execute("CREATE INDEX IF NOT EXISTS executable_events_run ON executable_events(run_id, sequence)")

    def close(self) -> None:
        self._db.close()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise ExecutableLedgerError("ledger clock must return a datetime")
        return _utc(value, "ledger clock")

    def _append(self, run_id: str, event_type: str, body: dict[str, object]) -> str:
        encoded = _canonical(body)
        row = self._db.execute("SELECT event_hash FROM executable_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = row[0] if row else _GENESIS
        digest = hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
        self._db.execute("INSERT INTO executable_events(run_id,event_type,body_json,previous_hash,event_hash) "
                         "VALUES(?,?,?,?,?)", (run_id, event_type, encoded, previous, digest))
        return digest

    def audit(self) -> None:
        previous = _GENESIS
        rows = self._db.execute("SELECT body_json,previous_hash,event_hash FROM executable_events ORDER BY sequence")
        for body, recorded_previous, recorded_hash in rows:
            digest = hashlib.sha256((previous + body).encode("utf-8")).hexdigest()
            if recorded_previous != previous or recorded_hash != digest:
                raise ExecutableLedgerError("executable selection ledger hash chain is corrupt")
            previous = digest

    def _events(self, run_id: str) -> list[tuple[str, dict[str, object]]]:
        rows = self._db.execute("SELECT event_type,body_json FROM executable_events "
                                "WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [(kind, json.loads(body)) for kind, body in rows]

    def freeze(self, run_id: str, *, selected: Iterable[tuple[date, str, str]],
               scheduled_days: Iterable[date], horizon_sessions: int, annual_cash_rate: float,
               missing_fill_loss: float = -1.0) -> str:
        """Persist exact picks and schedule before any outcome is available."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ExecutableLedgerError("run ID is required")
        now = self._now()
        picks = tuple(sorted((day.isoformat(), strategy, instrument) for day, strategy, instrument in selected))
        schedule = tuple(day.isoformat() for day in scheduled_days)
        if not picks or not schedule or len(set(picks)) != len(picks):
            raise ExecutableLedgerError("nonempty unique picks and schedule are required")
        if schedule != tuple(sorted(set(schedule))):
            raise ExecutableLedgerError("scheduled sessions must be unique and ordered")
        body: dict[str, object] = {"run_id": run_id, "registered_at": now.isoformat(),
                                   "selected": [list(row) for row in picks], "scheduled_days": list(schedule),
                                   "horizon_sessions": horizon_sessions, "annual_cash_rate": annual_cash_rate,
                                   "missing_fill_loss": missing_fill_loss}
        # Validate selection contract now, before outcome access.
        resolve_executable(selected=tuple((date.fromisoformat(day), strategy, instrument)
                                          for day, strategy, instrument in picks), outcomes=(),
                           scheduled_days=tuple(date.fromisoformat(day) for day in schedule),
                           horizon_sessions=horizon_sessions, annual_cash_rate=annual_cash_rate,
                           missing_fill_loss=missing_fill_loss)
        self.audit()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if self._db.execute("SELECT 1 FROM executable_events WHERE run_id=? LIMIT 1", (run_id,)).fetchone():
                raise ExecutableLedgerError("run ID is already registered")
            digest = self._append(run_id, "SELECTION_FROZEN", body)
            self._db.execute("COMMIT")
            return digest
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def score(self, run_id: str, *, outcomes: Iterable[ExecutableOutcome],
              outcome_available_at: datetime) -> dict[str, object]:
        """Score the immutable picks once; absent outcomes remain explicit worst-case misses."""
        available = _utc(outcome_available_at, "outcome availability")
        scored_at = self._now()
        if available > scored_at:
            raise ExecutableLedgerError("outcomes cannot be scored before their declared availability")
        self.audit()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            events = self._events(run_id)
            if len(events) != 1 or events[0][0] != "SELECTION_FROZEN":
                raise ExecutableLedgerError("run must have one frozen selection and no prior score")
            frozen = events[0][1]
            registered = datetime.fromisoformat(str(frozen["registered_at"]))
            if registered >= available:
                raise ExecutableLedgerError("selection was frozen after or when outcomes became available")
            selected_rows = cast(list[list[str]], frozen["selected"])
            schedule_rows = cast(list[str], frozen["scheduled_days"])
            result = resolve_executable(
                selected=tuple((date.fromisoformat(day), strategy, instrument)
                               for day, strategy, instrument in selected_rows),
                outcomes=tuple(outcomes),
                scheduled_days=tuple(date.fromisoformat(day) for day in schedule_rows),
                horizon_sessions=cast(int, frozen["horizon_sessions"]),
                annual_cash_rate=cast(float, frozen["annual_cash_rate"]),
                missing_fill_loss=cast(float, frozen["missing_fill_loss"]))
            body: dict[str, object] = {"run_id": run_id, "selection_event_sha256": self._db.execute(
                "SELECT event_hash FROM executable_events WHERE run_id=?", (run_id,)).fetchone()[0],
                "outcome_available_at": available.isoformat(), "scored_at": scored_at.isoformat(),
                "result": result}
            body["score_sha256"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
            self._append(run_id, "SCORED", body)
            self._db.execute("COMMIT")
            return body
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def result(self, run_id: str) -> dict[str, object] | None:
        """Read the persisted score without reopening outcome access."""
        self.audit()
        events = self._events(run_id)
        scored = [body for kind, body in events if kind == "SCORED"]
        return scored[0] if scored else None


__all__ = ["ExecutableLedgerError", "ExecutableSelectionLedger"]
