"""Durable normalized-event session runner foundation (PE-0017/0023)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.market_events import MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive
from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
)

IST = ZoneInfo("Asia/Kolkata")


class ForwardSessionRunnerError(ValueError):
    """Session runner rejected an event, decision or lifecycle operation."""


class ForwardSessionRunner:
    """Persist event and decision evidence from any adapter using normalized MarketEvent rows.

    This runner deliberately has no physical-order operation and does not create fills. The current
    implementation is the shared durable intake/decision journal boundary; risk, paper OMS, accounting,
    custody and close valuation still require integration before a full session can be claimed.
    """

    def __init__(self, state: ForwardSessionStore, journal_path: Path) -> None:
        self.state_store = state
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.journal_path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_events (
            session_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
            available_at TEXT NOT NULL, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL,
            PRIMARY KEY(session_id, event_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_decisions (
            session_id TEXT NOT NULL, decision_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
            outcome TEXT NOT NULL, decided_at TEXT NOT NULL, record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL, PRIMARY KEY(session_id, decision_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_session_reports (
            session_id TEXT PRIMARY KEY, status TEXT NOT NULL, report_sha256 TEXT NOT NULL,
            report_json TEXT NOT NULL)""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def begin(self, session_id: str, config: ForwardRunConfig, *, now: datetime,
              preflight: dict[str, bool]) -> SessionState:
        """Create or resume a session, recording every preflight check before opening it."""
        self.state_store.create(session_id, config, now=now)
        state = self.state_store.state(session_id)
        if state in {SessionState.OPEN, SessionState.REJECTED, SessionState.CLOSED, SessionState.INCOMPLETE}:
            return state
        if not preflight:
            raise ForwardSessionRunnerError("preflight must contain explicit checks")
        if any(not name.strip() or type(passed) is not bool for name, passed in preflight.items()):
            raise ForwardSessionRunnerError("preflight checks need non-empty names and boolean results")
        if state is SessionState.PLANNED:
            self.state_store.transition(session_id, SessionState.PREFLIGHT, now=now)
        elif state is not SessionState.PREFLIGHT:
            raise ForwardSessionRunnerError(f"cannot resume preflight from {state.value}")
        record = {"session_id": session_id, "event": "PREFLIGHT", "occurred_at": now.astimezone(timezone.utc).isoformat(),
                  "checks": dict(sorted(preflight.items())),
                  "config_sha256": config.sha256()}
        event_id = f"{session_id}:preflight"
        previous = self._con.execute("SELECT event_json FROM runner_events WHERE session_id=? AND event_id=?",
                                     [session_id, event_id]).fetchone()
        if previous:
            old = json.loads(previous[0])
            if any(old.get(key) != record.get(key) for key in ("session_id", "event", "checks", "config_sha256")):
                raise ForwardSessionRunnerError("preflight evidence conflicts with the prior attempt")
        else:
            self._append_record("preflight", session_id, record)
        failed = sorted(name for name, passed in preflight.items() if passed is not True)
        if failed:
            self.state_store.transition(session_id, SessionState.REJECTED, now=now,
                                        reason="failed preflight: " + ",".join(failed))
            return SessionState.REJECTED
        self.state_store.transition(session_id, SessionState.OPEN, now=now)
        return SessionState.OPEN

    def ingest(self, session_id: str, config: ForwardRunConfig,
               events: Iterable[MarketEvent]) -> dict[str, int]:
        """Durably accept normalized source events available during the frozen session."""
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("market events are accepted only in an OPEN session")
        expected = set(config.instrument_universe)
        counts = {"received": 0, "persisted": 0, "duplicate": 0}
        batch = list(events)
        self._con.execute("SAVEPOINT ingest_batch")
        try:
            for event in batch:
                counts["received"] += 1
                if event.instrument_id not in expected:
                    raise ForwardSessionRunnerError("event instrument is outside frozen universe")
                if event.event_time.astimezone(IST).date() != config.exchange_date:
                    raise ForwardSessionRunnerError("event exchange date differs from frozen session date")
                if event.available_at > event.received_at:
                    raise ForwardSessionRunnerError("event availability cannot be later than local receipt")
                payload = event.as_dict()
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
                digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                old = self._con.execute("SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
                                        [session_id, event.event_id]).fetchone()
                if old:
                    if old[0] != digest:
                        raise ForwardSessionRunnerError("event ID reused with conflicting content")
                    counts["duplicate"] += 1
                    continue
                self._con.execute("INSERT INTO runner_events VALUES (?, ?, ?, ?, ?, ?)",
                                  [session_id, event.event_id, event.kind.value,
                                   event.available_at.astimezone(timezone.utc).isoformat(), digest, canonical])
                counts["persisted"] += 1
        except Exception:
            self._con.execute("ROLLBACK TO ingest_batch")
            self._con.execute("RELEASE ingest_batch")
            self._con.rollback()
            raise
        self._con.execute("RELEASE ingest_batch")
        self._con.commit()
        return counts

    def ingest_kite_archive(self, session_id: str, config: ForwardRunConfig,
                             archive: QuoteArchive) -> dict[str, int]:
        """Actual-market adapter: read only archived Kite events, then use the canonical ingest path."""
        events = archive.events_for_session(config.exchange_date, source="KITE_CONNECT_READ_ONLY")
        return self.ingest(session_id, config, events)

    def decide(self, *, session_id: str, strategy_id: str, decision_id: str,
               outcome: str, decided_at: datetime, input_event_ids: tuple[str, ...],
               reason_codes: tuple[str, ...] = (), payload: dict[str, Any] | None = None) -> bool:
        """Record a decision only when every referenced source event was already available."""
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("decisions are accepted only in an OPEN session")
        if not all(value.strip() for value in (strategy_id, decision_id)):
            raise ForwardSessionRunnerError("strategy and decision IDs are required")
        if outcome not in {"TRADE", "NO_TRADE", "WITHHELD", "NO_SIGNAL"}:
            raise ForwardSessionRunnerError("unsupported forward decision outcome")
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ForwardSessionRunnerError("decision time must be timezone aware")
        if outcome != "TRADE" and not reason_codes:
            raise ForwardSessionRunnerError("non-trade decision requires a reason code")
        if not input_event_ids:
            raise ForwardSessionRunnerError("decision must reference at least one source event")
        unique_ids = tuple(sorted(set(input_event_ids)))
        if len(unique_ids) != len(input_event_ids):
            raise ForwardSessionRunnerError("decision input event IDs must be unique")
        rows = self._con.execute(
            f"SELECT event_id, available_at FROM runner_events WHERE session_id=? AND event_id IN ({','.join('?' for _ in unique_ids)})",
            [session_id, *unique_ids]).fetchall()
        if len(rows) != len(unique_ids):
            raise ForwardSessionRunnerError("decision references an event absent from this session")
        clock = decided_at.astimezone(timezone.utc)
        if any(datetime.fromisoformat(available) > clock for _, available in rows):
            raise ForwardSessionRunnerError("decision uses a source event unavailable at decision time")
        body = {"session_id": session_id, "strategy_id": strategy_id, "decision_id": decision_id,
                "outcome": outcome, "decided_at": clock.isoformat(), "input_event_ids": list(unique_ids),
                "reason_codes": sorted(set(reason_codes)), "payload": payload or {}}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self._con.execute("SELECT record_sha256 FROM runner_decisions WHERE session_id=? AND decision_id=?",
                                     [session_id, decision_id]).fetchone()
        if existing:
            if existing[0] != digest:
                raise ForwardSessionRunnerError("decision ID reused with conflicting content")
            return False
        self._con.execute("INSERT INTO runner_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [session_id, decision_id, strategy_id, outcome, clock.isoformat(), digest, canonical])
        self._con.commit()
        return True

    def records(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return the deterministic persisted event and decision trace for reanalysis."""
        events = [json.loads(row[0]) for row in self._con.execute(
            "SELECT event_json FROM runner_events WHERE session_id=? AND kind!='preflight' "
            "ORDER BY available_at, event_id",
            [session_id]).fetchall()]
        lifecycle = [json.loads(row[0]) for row in self._con.execute(
            "SELECT event_json FROM runner_events WHERE session_id=? AND kind='preflight' ORDER BY event_id",
            [session_id]).fetchall()]
        decisions = [json.loads(row[0]) for row in self._con.execute(
            "SELECT record_json FROM runner_decisions WHERE session_id=? ORDER BY decided_at, decision_id",
            [session_id]).fetchall()]
        return {"events": events, "decisions": decisions, "lifecycle": lifecycle}

    def close_session(self, session_id: str, *, now: datetime, complete: bool,
                      reason: str | None = None) -> dict[str, Any]:
        """Close evidence with a deterministic digest; incomplete sessions remain incomplete."""
        state = self.state_store.state(session_id)
        if state in {SessionState.CLOSED, SessionState.INCOMPLETE}:
            existing = self._con.execute("SELECT report_json FROM runner_session_reports WHERE session_id=?",
                                         [session_id]).fetchone()
            if existing:
                return json.loads(existing[0])
            raise ForwardSessionRunnerError("terminal session has no persisted close report")
        if state is not SessionState.OPEN:
            raise ForwardSessionRunnerError("only an OPEN session can close")
        self.state_store.transition(session_id, SessionState.DRAINING, now=now)
        if complete:
            self.state_store.transition(session_id, SessionState.CLOSED, now=now)
            status = "CLOSED"
        else:
            self.state_store.transition(session_id, SessionState.INCOMPLETE, now=now,
                                        reason=reason or "session evidence incomplete")
            status = "INCOMPLETE"
        records = self.records(session_id)
        body = {"session_id": session_id, "status": status, "closed_at": now.astimezone(timezone.utc).isoformat(),
                "event_count": len(records["events"]), "decision_count": len(records["decisions"]),
                "reason": reason, "records_sha256": hashlib.sha256(json.dumps(
                    records, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        report = body | {"report_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
        report_json = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._con.execute("INSERT INTO runner_session_reports VALUES (?, ?, ?, ?)",
                          [session_id, status, report["report_sha256"], report_json])
        self._con.commit()
        return report

    def _append_record(self, kind: str, session_id: str, record: dict[str, Any]) -> None:
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        event_id = f"{session_id}:{kind}"
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        previous = self._con.execute("SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
                                     [session_id, event_id]).fetchone()
        if previous and previous[0] != digest:
            raise ForwardSessionRunnerError("session lifecycle record conflicts with prior evidence")
        if not previous:
            self._con.execute("INSERT INTO runner_events VALUES (?, ?, ?, ?, ?, ?)",
                              [session_id, event_id, kind, record["occurred_at"], digest, canonical])
            self._con.commit()

