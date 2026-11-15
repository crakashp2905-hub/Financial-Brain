"""Versioned, durable forward-session identity and state machine (PE-0015/0016)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any


SCHEMA = "fb.forward-session.v1"
TRANSITIONS: dict[str, frozenset[str]] = {
    "PLANNED": frozenset({"PREFLIGHT", "NO_TRADE", "INCOMPLETE", "REJECTED"}),
    "PREFLIGHT": frozenset({"OPEN", "REJECTED", "INCOMPLETE"}),
    "OPEN": frozenset({"DRAINING", "INCOMPLETE"}),
    "DRAINING": frozenset({"CLOSED", "INCOMPLETE"}),
    "CLOSED": frozenset(),
    "NO_TRADE": frozenset(),
    "INCOMPLETE": frozenset(),
    "REJECTED": frozenset(),
}


class SessionStateError(ValueError):
    """Session identity or lifecycle transition is invalid."""


class SessionState(StrEnum):
    PLANNED = "PLANNED"
    PREFLIGHT = "PREFLIGHT"
    OPEN = "OPEN"
    DRAINING = "DRAINING"
    CLOSED = "CLOSED"
    NO_TRADE = "NO_TRADE"
    INCOMPLETE = "INCOMPLETE"
    REJECTED = "REJECTED"


def session_attempt_key(*, strategy_sha256: str, exchange_date: date, cohort: str,
                        mode: str, attempt_id: str) -> str:
    """Return a stable, immutable key for one strategy/date/cohort/mode attempt.

    Repeating the same attempt identity is idempotent; a deliberate rerun must use a
    fresh ``attempt_id`` and therefore cannot overwrite the earlier session record.
    The digest avoids unsafe or excessively long caller supplied identifiers.
    """
    if len(strategy_sha256) != 64 or any(c not in "0123456789abcdef" for c in strategy_sha256):
        raise SessionStateError("strategy_sha256 must be lowercase SHA-256")
    if not isinstance(exchange_date, date):
        raise SessionStateError("exchange_date must be a date")
    if not all(isinstance(value, str) and value.strip()
               for value in (cohort, mode, attempt_id)):
        raise SessionStateError("cohort, mode and attempt_id are required")
    body = {"strategy_sha256": strategy_sha256, "exchange_date": exchange_date.isoformat(),
            "cohort": cohort, "mode": mode, "attempt_id": attempt_id}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "forward-attempt:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ForwardRunConfig:
    exchange_date: date
    cohort: str
    strategy_sha256: str
    benchmark: str
    instrument_universe: tuple[str, ...]
    capital: Decimal
    constraints: dict[str, Any]
    ledger_id: str
    mode: str = "paper"
    attempt_id: str = "default"
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise SessionStateError(f"unsupported run config schema: {self.schema}")
        if not all(value.strip() for value in (self.cohort, self.benchmark, self.ledger_id,
                                               self.mode, self.attempt_id)):
            raise SessionStateError("cohort, benchmark, ledger_id, mode and attempt_id are required")
        if len(self.strategy_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.strategy_sha256):
            raise SessionStateError("strategy_sha256 must be lowercase SHA-256")
        try:
            amount = Decimal(self.capital)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise SessionStateError("capital must be a finite positive decimal") from exc
        if not amount.is_finite() or amount <= 0:
            raise SessionStateError("capital must be a finite positive decimal")
        if not self.instrument_universe or any(not x.strip() for x in self.instrument_universe):
            raise SessionStateError("instrument universe must contain non-empty IDs")
        if len(set(self.instrument_universe)) != len(self.instrument_universe):
            raise SessionStateError("instrument universe contains duplicates")
        try:
            json.dumps(self.constraints, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise SessionStateError("constraints must be finite canonical JSON data") from exc

    def body(self) -> dict[str, Any]:
        return {"schema": self.schema, "exchange_date": self.exchange_date.isoformat(),
                "cohort": self.cohort, "strategy_sha256": self.strategy_sha256,
                "benchmark": self.benchmark,
                "instrument_universe": sorted(self.instrument_universe),
                "capital": format(Decimal(self.capital).normalize(), "f"),
                "constraints": self.constraints, "ledger_id": self.ledger_id,
                "mode": self.mode, "attempt_id": self.attempt_id}

    def canonical_json(self) -> str:
        return json.dumps(self.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ForwardSessionStore:
    """SQLite-backed append-only state history with exact config binding and idempotency."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS forward_sessions (
            session_id TEXT PRIMARY KEY, config_sha256 TEXT NOT NULL, config_json TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS forward_session_events (
            session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
            prior_state TEXT, state TEXT NOT NULL, occurred_at TEXT NOT NULL, reason TEXT,
            event_sha256 TEXT NOT NULL, PRIMARY KEY(session_id, sequence))""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def create(self, session_id: str, config: ForwardRunConfig, *, now: datetime) -> bool:
        if not session_id.strip() or now.tzinfo is None or now.utcoffset() is None:
            raise SessionStateError("session_id and timezone-aware creation clock are required")
        digest, payload = config.sha256(), config.canonical_json()
        previous = self._con.execute("SELECT config_sha256 FROM forward_sessions WHERE session_id=?",
                                     [session_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise SessionStateError("session_id cannot be reused with different config metadata")
            return False
        self._con.execute("INSERT INTO forward_sessions VALUES (?, ?, ?, ?)",
                          [session_id, digest, payload, now.astimezone(timezone.utc).isoformat()])
        self._con.execute("INSERT INTO forward_session_events VALUES (?, 0, ?, NULL, ?, ?, NULL, ?)",
                          [session_id, f"{session_id}:0:PLANNED", SessionState.PLANNED.value,
                           now.astimezone(timezone.utc).isoformat(),
                           self._event_hash(session_id, 0, None, SessionState.PLANNED.value, now, None)])
        self._con.commit()
        return True

    @staticmethod
    def _event_hash(session_id: str, sequence: int, prior: str | None, state: str,
                    occurred_at: datetime, reason: str | None) -> str:
        body = {"session_id": session_id, "sequence": sequence, "prior_state": prior,
                "state": state, "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
                "reason": reason}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()

    def transition(self, session_id: str, state: SessionState, *, now: datetime,
                   reason: str | None = None, event_id: str | None = None) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise SessionStateError("transition clock must be timezone aware")
        if not isinstance(state, SessionState):
            raise SessionStateError("state must be a SessionState")
        rows = self._con.execute("SELECT sequence, state, occurred_at FROM forward_session_events "
                                 "WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()
        if not rows:
            raise SessionStateError("unknown forward session")
        sequence, prior, prior_time = rows[-1]
        stable_event_id = event_id or f"{session_id}:{sequence}:{state.value}"
        duplicate = self._con.execute("SELECT sequence, prior_state, occurred_at, reason, event_sha256 "
                                      "FROM forward_session_events WHERE event_id=?",
                                      [stable_event_id]).fetchone()
        if duplicate:
            duplicate_hash = self._event_hash(session_id, int(duplicate[0]), duplicate[1], state.value,
                                              now, reason)
            if duplicate[4] != duplicate_hash or duplicate[2] != now.astimezone(timezone.utc).isoformat() or \
                    duplicate[3] != reason:
                raise SessionStateError("event ID reused with different session transition")
            return False
        if state.value not in TRANSITIONS[prior]:
            raise SessionStateError(f"invalid forward-session transition {prior} -> {state.value}")
        occurred = now.astimezone(timezone.utc)
        if occurred < datetime.fromisoformat(prior_time):
            raise SessionStateError("session transition clock cannot move backwards")
        if state in {SessionState.INCOMPLETE, SessionState.REJECTED, SessionState.NO_TRADE} and not (reason or "").strip():
            raise SessionStateError(f"{state.value} transition requires a reason")
        next_seq = int(sequence) + 1
        stable_event_id = event_id or f"{session_id}:{next_seq}:{state.value}"
        digest = self._event_hash(session_id, next_seq, prior, state.value, occurred, reason)
        self._con.execute("INSERT INTO forward_session_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          [session_id, next_seq, stable_event_id, prior, state.value,
                           occurred.isoformat(), reason, digest])
        self._con.commit()
        return True

    def state(self, session_id: str) -> SessionState:
        row = self._con.execute("SELECT state FROM forward_session_events WHERE session_id=? "
                                "ORDER BY sequence DESC LIMIT 1", [session_id]).fetchone()
        if row is None:
            raise SessionStateError("unknown forward session")
        return SessionState(row[0])

    def config_sha256(self, session_id: str) -> str:
        row = self._con.execute("SELECT config_sha256 FROM forward_sessions WHERE session_id=?",
                                [session_id]).fetchone()
        if row is None:
            raise SessionStateError("unknown forward session")
        return str(row[0])

    def config(self, session_id: str) -> ForwardRunConfig:
        """Rehydrate the immutable run configuration committed when the session was created."""
        row = self._con.execute("SELECT config_json FROM forward_sessions WHERE session_id=?",
                                [session_id]).fetchone()
        if row is None:
            raise SessionStateError("unknown forward session")
        body = json.loads(row[0])
        return ForwardRunConfig(
            exchange_date=date.fromisoformat(body["exchange_date"]), cohort=body["cohort"],
            strategy_sha256=body["strategy_sha256"], benchmark=body["benchmark"],
            instrument_universe=tuple(body["instrument_universe"]), capital=Decimal(body["capital"]),
            constraints=body["constraints"], ledger_id=body["ledger_id"],
            mode=body["mode"], attempt_id=body["attempt_id"], schema=body["schema"])
