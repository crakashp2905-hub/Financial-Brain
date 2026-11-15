"""Durable normalized-event session runner foundation (PE-0017/0023)."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.execution_scenario import evaluate_scenario
from financial_brain.forward_edge.clock import ClockProvider, SystemClock, capture_local_time
from financial_brain.forward_edge.intent_timing import create_timed_intent
from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive
from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
)
from financial_brain.forward_edge.venue_calendar import VenueCalendar

IST = ZoneInfo("Asia/Kolkata")


class ForwardSessionRunnerError(ValueError):
    """Session runner rejected an event, decision or lifecycle operation."""


class ForwardSessionRunner:
    """Persist event and decision evidence from any adapter using normalized MarketEvent rows.

    This runner deliberately has no physical-order operation and does not create fills. The current
    implementation is the shared durable intake/decision journal boundary; risk, paper OMS, accounting,
    custody and close valuation still require integration before a full session can be claimed.
    """

    def __init__(self, state: ForwardSessionStore, journal_path: Path, *,
                 clock: ClockProvider | None = None) -> None:
        self.state_store = state
        self.clock = clock or SystemClock()
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.journal_path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_events (
            session_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
            available_at TEXT NOT NULL, ingested_at TEXT, ingest_clock_source TEXT,
            event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL,
            PRIMARY KEY(session_id, event_id))""")
        event_columns = {row[1] for row in self._con.execute("PRAGMA table_info(runner_events)")}
        if "ingested_at" not in event_columns:
            self._con.execute("ALTER TABLE runner_events ADD COLUMN ingested_at TEXT")
        if "ingest_clock_source" not in event_columns:
            self._con.execute("ALTER TABLE runner_events ADD COLUMN ingest_clock_source TEXT")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_decisions (
            session_id TEXT NOT NULL, decision_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
            outcome TEXT NOT NULL, decided_at TEXT NOT NULL, record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL, PRIMARY KEY(session_id, decision_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_risk_verdicts (
            session_id TEXT NOT NULL, decision_id TEXT NOT NULL, verdict_id TEXT NOT NULL,
            allowed INTEGER NOT NULL, record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
            PRIMARY KEY(session_id, decision_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_data_rejections (
            session_id TEXT NOT NULL, rejection_id TEXT NOT NULL, record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL, PRIMARY KEY(session_id, rejection_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_audit_log (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            record_id TEXT NOT NULL, kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
            record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
            UNIQUE(session_id, record_id))""")
        self._con.executescript("""
            CREATE TRIGGER IF NOT EXISTS runner_audit_no_update
            BEFORE UPDATE ON runner_audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS runner_audit_no_delete
            BEFORE DELETE ON runner_audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
        """)
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_session_reports (
            session_id TEXT PRIMARY KEY, status TEXT NOT NULL, report_sha256 TEXT NOT NULL,
            report_json TEXT NOT NULL)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_checkpoints (
            session_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, phase TEXT NOT NULL,
            checkpoint_sha256 TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
            PRIMARY KEY(session_id, checkpoint_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS paper_intents (
            session_id TEXT NOT NULL, order_id TEXT NOT NULL, decision_id TEXT NOT NULL,
            event_id TEXT NOT NULL, request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,
            PRIMARY KEY(session_id, order_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS paper_execution_receipts (
            session_id TEXT NOT NULL, order_id TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
            receipt_json TEXT NOT NULL, PRIMARY KEY(session_id, order_id))""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()
        self.state_store.close()

    def begin(self, session_id: str, config: ForwardRunConfig, *, now: datetime | None = None,
              preflight: dict[str, bool]) -> SessionState:
        """Create or resume a session, recording every preflight check before opening it."""
        now = now or capture_local_time(self.clock, label="session start")
        self.state_store.create(session_id, config, now=now)
        state = self.state_store.state(session_id)
        if state in {SessionState.OPEN, SessionState.REJECTED, SessionState.CLOSED,
                     SessionState.NO_TRADE, SessionState.INCOMPLETE}:
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

    def plan_calendar_day(self, session_id: str, config: ForwardRunConfig, *, venue: str,
                          calendar: VenueCalendar, now: datetime, preflight: dict[str, bool],
                          supported_session_types: frozenset[str] = frozenset({"NORMAL"})) -> SessionState:
        """Turn a source-covered venue date into an open session, NO_TRADE, or INCOMPLETE."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ForwardSessionRunnerError("calendar planning clock must be timezone aware")
        try:
            calendar.is_session(venue, calendar.source_as_of)  # validates the venue contract and calendar horizon
        except ValueError as exc:
            raise ForwardSessionRunnerError(str(exc)) from exc
        self.state_store.create(session_id, config, now=now)
        state = self.state_store.state(session_id)
        calendar_digest = calendar.sha256()
        if state is not SessionState.PLANNED:
            prior = self._con.execute("SELECT event_json FROM runner_events "
                                      "WHERE session_id=? AND kind='calendar_plan'", [session_id]).fetchone()
            if prior is None or json.loads(prior[0]).get("calendar_sha256") != calendar_digest:
                raise ForwardSessionRunnerError("session already has state without matching calendar-plan evidence")
            return state
        if config.exchange_date > calendar.source_as_of:
            body = {"session_id": session_id, "event": "CALENDAR_PLAN", "outcome": "INCOMPLETE",
                    "exchange_date": config.exchange_date.isoformat(), "venue": venue,
                    "calendar_sha256": calendar_digest, "source_as_of": calendar.source_as_of.isoformat(),
                    "occurred_at": now.astimezone(timezone.utc).isoformat(),
                    "reason": "official session calendar does not cover the requested date"}
            self._append_record("calendar_plan", session_id, body)
            self.state_store.transition(session_id, SessionState.INCOMPLETE, now=now,
                                        reason=body["reason"])
            return SessionState.INCOMPLETE
        market_session = calendar.sessions.get((venue, config.exchange_date))
        if market_session is None:
            body = {"session_id": session_id, "event": "CALENDAR_PLAN", "outcome": "NO_TRADE",
                    "exchange_date": config.exchange_date.isoformat(), "venue": venue,
                    "calendar_sha256": calendar_digest, "source_as_of": calendar.source_as_of.isoformat(),
                    "occurred_at": now.astimezone(timezone.utc).isoformat(),
                    "reason": "no venue session appears in the source-covered calendar"}
            self._append_record("calendar_plan", session_id, body)
            self.state_store.transition(session_id, SessionState.NO_TRADE, now=now,
                                        reason=body["reason"])
            return SessionState.NO_TRADE
        session_type = market_session.session_type.strip().upper()
        opens, closes = calendar.session_bounds_utc(venue, config.exchange_date)
        supported = {value.strip().upper() for value in supported_session_types}
        body = {"session_id": session_id, "event": "CALENDAR_PLAN",
                "outcome": "OPEN" if session_type in supported else "NO_TRADE",
                "exchange_date": config.exchange_date.isoformat(), "venue": venue,
                "session_type": session_type, "opens_at": opens.isoformat(), "closes_at": closes.isoformat(),
                "calendar_sha256": calendar_digest, "source_sha256": market_session.source_sha256,
                "source_ref": market_session.source_ref,
                "occurred_at": now.astimezone(timezone.utc).isoformat()}
        self._append_record("calendar_plan", session_id, body)
        if session_type not in supported:
            self.state_store.transition(session_id, SessionState.NO_TRADE, now=now,
                                        reason=f"unsupported venue session type {session_type}")
            return SessionState.NO_TRADE
        return self.begin(session_id, config, now=now, preflight=preflight)

    def resume(self, session_id: str) -> dict[str, Any]:
        """Reopen durable session context after a process restart without replaying side effects."""
        config = self.state_store.config(session_id)
        if self.state_store.config_sha256(session_id) != config.sha256():
            raise ForwardSessionRunnerError("stored session configuration hash does not match its contents")
        state = self.state_store.state(session_id)
        records = self.records(session_id)
        if state is SessionState.PREFLIGHT:
            preflight = next((item.get("checks") for item in records["lifecycle"]
                              if item.get("event") == "PREFLIGHT"), None)
            if not isinstance(preflight, dict) or not preflight:
                raise ForwardSessionRunnerError("cannot resume PREFLIGHT without durable preflight checks")
            state = self.begin(session_id, config, preflight=preflight)
        elif state is SessionState.DRAINING:
            try:
                self.close_session(session_id, complete=True)
            except ForwardSessionRunnerError as exc:
                # An unresolved or incomplete close becomes durable INCOMPLETE, never an inferred success.
                self.close_session(session_id, complete=False,
                                   reason=f"restart recovery could not attest a complete close: {exc}")
            state = self.state_store.state(session_id)

        unknown: list[str] = []
        outbox_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        if outbox_path.is_file():
            with sqlite3.connect(str(outbox_path)) as outbox:
                unknown = [row[0] for row in outbox.execute(
                    "SELECT key FROM outbox WHERE state IN ('PENDING','UNKNOWN') ORDER BY key")
                           if row[0].startswith(f"paper:{session_id}:")]
        return {"session_id": session_id, "state": state.value,
                "config_sha256": config.sha256(), "records": self.records(session_id),
                "unresolved_paper_outbox_keys": unknown,
                "manual_reconciliation_required": bool(unknown),
                "physical_orders_enabled": False}

    def ingest(self, session_id: str, config: ForwardRunConfig,
               events: Iterable[MarketEvent], *,
               ingest_metadata: dict[str, tuple[datetime, str]] | None = None) -> dict[str, int]:
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
                calendar_plan = self._con.execute(
                    "SELECT event_json FROM runner_events WHERE session_id=? AND kind='calendar_plan'",
                    [session_id]).fetchone()
                if calendar_plan:
                    planned = json.loads(calendar_plan[0])
                    if planned.get("outcome") == "OPEN":
                        event_time = event.event_time.astimezone(timezone.utc)
                        if (event_time < datetime.fromisoformat(planned["opens_at"]) or
                                event_time >= datetime.fromisoformat(planned["closes_at"])):
                            raise ForwardSessionRunnerError("event exchange time is outside the sourced venue session")
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
                metadata = (ingest_metadata or {}).get(event.event_id)
                if metadata is None:
                    ingested_at = capture_local_time(self.clock, label="event ingest timestamp")
                    ingest_source = "SESSION_RUNNER_CLOCK"
                else:
                    ingested_at, ingest_source = metadata
                if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
                    raise ForwardSessionRunnerError("event ingest timestamp must be timezone aware")
                if not ingest_source.strip():
                    raise ForwardSessionRunnerError("event ingest clock source is required")
                ingested_at = ingested_at.astimezone(timezone.utc)
                if ingested_at < event.available_at:
                    raise ForwardSessionRunnerError("event ingest time cannot precede availability")
                self._con.execute("""INSERT INTO runner_events (
                                    session_id, event_id, kind, available_at, ingested_at,
                                    ingest_clock_source, event_sha256, event_json)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                  [session_id, event.event_id, event.kind.value,
                                   event.available_at.astimezone(timezone.utc).isoformat(),
                                   ingested_at.isoformat(), ingest_source, digest, canonical])
                self._append_audit("market_event", session_id, event.event_id, ingested_at,
                                   {"event": payload, "event_sha256": digest,
                                    "ingested_at": ingested_at.isoformat(),
                                    "ingest_clock_source": ingest_source})
                counts["persisted"] += 1
        except Exception as exc:
            self._con.execute("ROLLBACK TO ingest_batch")
            self._con.execute("RELEASE ingest_batch")
            self._con.rollback()
            self._record_data_rejection(session_id, "ingest_batch", str(exc), batch)
            raise
        self._con.execute("RELEASE ingest_batch")
        self._con.commit()
        return counts

    def ingest_kite_archive(self, session_id: str, config: ForwardRunConfig,
                             archive: QuoteArchive) -> dict[str, int]:
        """Actual-market adapter: read only archived Kite events, then use the canonical ingest path."""
        events = archive.events_for_session(config.exchange_date, source="KITE_CONNECT_READ_ONLY")
        metadata = {}
        for event in events:
            ingested_at = archive.ingest_time(event.event_id)
            metadata[event.event_id] = (
                (ingested_at, "QUOTE_ARCHIVE") if ingested_at is not None else
                (capture_local_time(self.clock, label="legacy event import timestamp"),
                 "LEGACY_ARCHIVE_IMPORT"))
        return self.ingest(session_id, config, events, ingest_metadata=metadata)

    def decide(self, *, session_id: str, strategy_id: str, decision_id: str,
               outcome: str, input_event_ids: tuple[str, ...], decided_at: datetime | None = None,
               reason_codes: tuple[str, ...] = (), payload: dict[str, Any] | None = None) -> bool:
        """Record a decision only when every referenced source event was already available."""
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("decisions are accepted only in an OPEN session")
        if not all(value.strip() for value in (strategy_id, decision_id)):
            raise ForwardSessionRunnerError("strategy and decision IDs are required")
        if outcome not in {"TRADE", "NO_TRADE", "WITHHELD", "NO_SIGNAL"}:
            raise ForwardSessionRunnerError("unsupported forward decision outcome")
        decided_at = decided_at or capture_local_time(self.clock, label="decision timestamp")
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
            f"SELECT event_id, available_at FROM runner_events WHERE session_id=? "
            f"AND kind NOT IN ('preflight','calendar_plan') AND event_id IN ({','.join('?' for _ in unique_ids)})",
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
        self._append_audit("decision", session_id, decision_id, clock, body)
        self._con.commit()
        return True

    def record_risk_verdict(self, *, session_id: str, decision_id: str, verdict_id: str,
                            allowed: bool, policy_id: str, policy_sha256: str,
                            reason_codes: tuple[str, ...] = (), assessed_at: datetime | None = None) -> bool:
        """Persist the immutable policy verdict before any paper order intent."""
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("risk verdicts are accepted only in an OPEN session")
        if not all(value.strip() for value in (decision_id, verdict_id, policy_id)):
            raise ForwardSessionRunnerError("risk verdict identity and policy are required")
        if type(allowed) is not bool or re.fullmatch(r"[0-9a-f]{64}", policy_sha256) is None:
            raise ForwardSessionRunnerError("risk verdict needs an explicit boolean and policy SHA-256")
        decision = self._con.execute(
            "SELECT outcome, decided_at FROM runner_decisions WHERE session_id=? AND decision_id=?",
            [session_id, decision_id]).fetchone()
        if decision is None or decision[0] != "TRADE":
            raise ForwardSessionRunnerError("risk verdict requires a journaled TRADE decision")
        assessed_at = assessed_at or capture_local_time(self.clock, label="risk assessment timestamp")
        if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
            raise ForwardSessionRunnerError("risk assessment time must be timezone aware")
        occurred = assessed_at.astimezone(timezone.utc)
        if occurred < datetime.fromisoformat(decision[1]):
            raise ForwardSessionRunnerError("risk verdict cannot precede its decision")
        record = {"session_id": session_id, "decision_id": decision_id, "verdict_id": verdict_id,
                  "allowed": allowed, "policy_id": policy_id, "policy_sha256": policy_sha256,
                  "reason_codes": sorted(set(reason_codes)), "assessed_at": occurred.isoformat()}
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        previous = self._con.execute("SELECT record_sha256 FROM runner_risk_verdicts "
                                      "WHERE session_id=? AND decision_id=?", [session_id, decision_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise ForwardSessionRunnerError("risk verdict conflicts with the immutable prior verdict")
            return False
        self._con.execute("INSERT INTO runner_risk_verdicts VALUES (?, ?, ?, ?, ?, ?)",
                          [session_id, decision_id, verdict_id, int(allowed), digest, canonical])
        self._append_audit("risk_verdict", session_id, verdict_id, occurred, record)
        self._con.commit()
        return True

    def records(self, session_id: str) -> dict[str, Any]:
        """Return the deterministic persisted event and decision trace for reanalysis."""
        events = [json.loads(row[0]) | {"ingested_at": row[1], "ingest_clock_source": row[2]}
                  for row in self._con.execute(
            "SELECT event_json, ingested_at, ingest_clock_source FROM runner_events "
            "WHERE session_id=? AND kind NOT IN ('preflight','calendar_plan') "
            "ORDER BY available_at, event_id",
            [session_id]).fetchall()]
        lifecycle = [json.loads(row[0]) for row in self._con.execute(
            "SELECT event_json FROM runner_events WHERE session_id=? AND kind IN ('preflight','calendar_plan') "
            "ORDER BY available_at, event_id",
            [session_id]).fetchall()]
        decisions = [json.loads(row[0]) for row in self._con.execute(
            "SELECT record_json FROM runner_decisions WHERE session_id=? ORDER BY decided_at, decision_id",
            [session_id]).fetchall()]
        intents = [json.loads(row[0]) for row in self._con.execute(
            "SELECT request_json FROM paper_intents WHERE session_id=? ORDER BY order_id",
            [session_id]).fetchall()]
        receipts = [json.loads(row[0]) for row in self._con.execute(
            "SELECT receipt_json FROM paper_execution_receipts WHERE session_id=? ORDER BY rowid",
            [session_id]).fetchall()]
        reports = [json.loads(row[0]) for row in self._con.execute(
            "SELECT report_json FROM runner_session_reports WHERE session_id=? ORDER BY session_id",
            [session_id]).fetchall()]
        accounting = self._rebuild_accounting(session_id, events, intents, receipts)
        risk_verdicts = [json.loads(row[0]) for row in self._con.execute(
            "SELECT record_json FROM runner_risk_verdicts WHERE session_id=? ORDER BY decision_id",
            [session_id]).fetchall()]
        data_rejections = [json.loads(row[0]) for row in self._con.execute(
            "SELECT record_json FROM runner_data_rejections WHERE session_id=? ORDER BY rejection_id",
            [session_id]).fetchall()]
        audit_log = [{"kind": row[0], "record_id": row[1], "occurred_at": row[2],
                      "record_sha256": row[3], "record": json.loads(row[4])}
                     for row in self._con.execute(
            "SELECT kind, record_id, occurred_at, record_sha256, record_json "
            "FROM runner_audit_log WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()]
        return {"events": events, "decisions": decisions, "lifecycle": lifecycle,
                "intents": intents, "execution_receipts": receipts, "session_reports": reports,
                "risk_verdicts": risk_verdicts, "data_rejections": data_rejections,
                "audit_log": audit_log, "accounting": accounting}

    def rebuild_materialized_views(self, session_id: str) -> None:
        """Rebuild query tables for a day from the immutable append-only audit log."""
        audit = self._con.execute(
            "SELECT kind, record_json FROM runner_audit_log WHERE session_id=? ORDER BY sequence",
            [session_id]).fetchall()
        if not audit:
            raise ForwardSessionRunnerError("cannot rebuild session views without canonical audit events")
        self._con.commit()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            for table in ("runner_events", "runner_decisions", "runner_risk_verdicts",
                          "runner_data_rejections", "paper_intents", "paper_execution_receipts",
                          "runner_session_reports"):
                self._con.execute(f"DELETE FROM {table} WHERE session_id=?", [session_id])
            for kind, encoded in audit:
                body = json.loads(encoded)
                if kind in {"preflight", "calendar_plan"}:
                    event_id = f"{session_id}:{kind}"
                    digest = self._sha256(body)
                    self._con.execute("INSERT INTO runner_events "
                                      "(session_id,event_id,kind,available_at,event_sha256,event_json) "
                                      "VALUES (?,?,?,?,?,?)",
                                      [session_id, event_id, kind, body["occurred_at"], digest, encoded])
                elif kind == "market_event":
                    event = body["event"]
                    self._con.execute("INSERT INTO runner_events "
                                      "(session_id,event_id,kind,available_at,ingested_at,ingest_clock_source,"
                                      "event_sha256,event_json) VALUES (?,?,?,?,?,?,?,?)",
                                      [session_id, event["event_id"], event["kind"], event["available_at"],
                                       body["ingested_at"], body["ingest_clock_source"],
                                       body["event_sha256"], json.dumps(event, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)])
                elif kind == "decision":
                    encoded_body = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    self._con.execute("INSERT INTO runner_decisions VALUES (?,?,?,?,?,?,?)",
                                      [session_id, body["decision_id"], body["strategy_id"], body["outcome"],
                                       body["decided_at"], self._sha256(body), encoded_body])
                elif kind == "risk_verdict":
                    self._con.execute("INSERT INTO runner_risk_verdicts VALUES (?,?,?,?,?,?)",
                                      [session_id, body["decision_id"], body["verdict_id"],
                                       int(body["allowed"]), self._sha256(body), encoded])
                elif kind == "data_rejection":
                    self._con.execute("INSERT INTO runner_data_rejections VALUES (?,?,?,?)",
                                      [session_id, body["rejection_id"], self._sha256(body), encoded])
                elif kind == "paper_intent":
                    request = body
                    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    request_hash = self._sha256(request)
                    self._con.execute("INSERT INTO paper_intents VALUES (?,?,?,?,?,?)",
                                      [session_id, request["order_id"], request["decision_id"],
                                       request["event_id"], request_hash, request_json])
                elif kind == "execution_receipt":
                    encoded_body = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    self._con.execute("INSERT INTO paper_execution_receipts VALUES (?,?,?,?)",
                                      [session_id, body["order_id"], self._sha256(body), encoded_body])
                elif kind == "session_report":
                    encoded_body = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    self._con.execute("INSERT INTO runner_session_reports VALUES (?,?,?,?)",
                                      [session_id, body["status"], body["report_sha256"], encoded_body])
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise

    def _rebuild_accounting(self, session_id: str, events: list[dict[str, Any]],
                            intents: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> dict[str, Any]:
        from financial_brain.forward_edge.paper_accounting import rebuild_paper_accounting

        config = self.state_store.config(session_id)
        return rebuild_paper_accounting(account_id=config.ledger_id, capital=Decimal(config.capital),
                                        events=events, intents=intents, receipts=receipts)

    def checkpoint(self, session_id: str, *, phase: str, now: datetime | None = None) -> dict[str, Any]:
        """Freeze a START or END journal/outbox snapshot; ledger absence is explicit."""
        phase = phase.strip().upper()
        if phase not in {"START", "END"}:
            raise ForwardSessionRunnerError("checkpoint phase must be START or END")
        now = now or capture_local_time(self.clock, label="checkpoint timestamp")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ForwardSessionRunnerError("checkpoint timestamp must be timezone aware")
        checkpoint_id = f"{session_id}:{phase.lower()}"
        existing = self._con.execute("SELECT checkpoint_sha256, checkpoint_json FROM runner_checkpoints "
                                     "WHERE session_id=? AND checkpoint_id=?",
                                     [session_id, checkpoint_id]).fetchone()
        if existing and phase == "START":
            return json.loads(existing[1]) | {"checkpoint_sha256": existing[0]}
        state = self.state_store.state(session_id)
        records = self.records(session_id)
        if phase == "START" and any(records[name] for name in ("events", "decisions", "intents", "execution_receipts")):
            raise ForwardSessionRunnerError("START checkpoint must precede market events and decisions")
        if phase == "END" and state not in {SessionState.CLOSED, SessionState.NO_TRADE, SessionState.INCOMPLETE}:
            raise ForwardSessionRunnerError("END checkpoint requires a terminal session state")
        outbox_rows: list[dict[str, Any]] = []
        outbox_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        if outbox_path.is_file():
            with sqlite3.connect(str(outbox_path)) as outbox:
                outbox_rows = [{"key": row[0], "state": row[1], "reconciled_by": row[2]}
                               for row in outbox.execute(
                                   "SELECT key, state, reconciled_by FROM outbox "
                                   "WHERE key LIKE ? ORDER BY key", [f"paper:{session_id}:%"]).fetchall()]
        hashes = {name: self._sha256(records[name])
                  for name in ("events", "decisions", "lifecycle", "intents", "execution_receipts")}
        hashes["accounting"] = self._sha256(records["accounting"])
        hashes["ledger"] = str(records["accounting"]["ledger_sha256"])
        hashes["outbox"] = self._sha256(outbox_rows)
        if existing:
            prior = json.loads(existing[1])
            if prior.get("session_state") != state.value or prior.get("record_hashes") != hashes:
                raise ForwardSessionRunnerError("END checkpoint ID already contains different immutable evidence")
            return prior | {"checkpoint_sha256": existing[0]}
        body = {"schema": "fb.forward-session-checkpoint.v1", "session_id": session_id,
                "checkpoint_id": checkpoint_id, "phase": phase,
                "captured_at": now.astimezone(timezone.utc).isoformat(),
                "session_state": state.value, "record_hashes": hashes,
                "ledger_sha256": records["accounting"]["ledger_sha256"],
                "ledger_status": records["accounting"]["status"],
                "atomic_with_ledger": False}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._con.execute("INSERT INTO runner_checkpoints VALUES (?, ?, ?, ?, ?)",
                          [session_id, checkpoint_id, phase, digest, canonical])
        self._con.commit()
        return body | {"checkpoint_sha256": digest}

    def verify_checkpoint(self, session_id: str, checkpoint_id: str) -> dict[str, Any]:
        """Verify immutable checkpoint bytes and, for END, compare them with current journal contents."""
        row = self._con.execute("SELECT phase, checkpoint_sha256, checkpoint_json FROM runner_checkpoints "
                                "WHERE session_id=? AND checkpoint_id=?",
                                [session_id, checkpoint_id]).fetchone()
        if row is None:
            raise ForwardSessionRunnerError("unknown checkpoint")
        phase, expected_digest, encoded = row
        actual_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        body = json.loads(encoded)
        immutable_ok = actual_digest == expected_digest
        current_ok: bool | None = None
        if phase == "END" and immutable_ok:
            current = self._checkpoint_hashes(session_id)
            current_ok = current == body["record_hashes"]
        return {"session_id": session_id, "checkpoint_id": checkpoint_id,
                "immutable_evidence_valid": immutable_ok, "current_records_match": current_ok,
                "ledger_sha256": body["ledger_sha256"], "ledger_status": body["ledger_status"],
                "verification_complete": immutable_ok and current_ok is True and
                body["ledger_sha256"] is not None and body["atomic_with_ledger"] is True}

    def _checkpoint_hashes(self, session_id: str) -> dict[str, str]:
        records = self.records(session_id)
        hashes = {name: self._sha256(records[name])
                  for name in ("events", "decisions", "lifecycle", "intents", "execution_receipts")}
        hashes["accounting"] = self._sha256(records["accounting"])
        hashes["ledger"] = str(records["accounting"]["ledger_sha256"])
        outbox_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        outbox_rows: list[dict[str, Any]] = []
        if outbox_path.is_file():
            with sqlite3.connect(str(outbox_path)) as outbox:
                outbox_rows = [{"key": row[0], "state": row[1], "reconciled_by": row[2]}
                               for row in outbox.execute(
                                   "SELECT key, state, reconciled_by FROM outbox "
                                   "WHERE key LIKE ? ORDER BY key", [f"paper:{session_id}:%"]).fetchall()]
        hashes["outbox"] = self._sha256(outbox_rows)
        return hashes

    @staticmethod
    def _sha256(value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def execute_paper_intent(self, *, session_id: str, order_id: str, decision_id: str,
                             event_id: str, side: str, quantity: int, limit_price: Decimal,
                             tick_size: Decimal, freeze_limit: int, slice_size: int,
                             adverse_slippage_bps: Decimal, latency_ms: int,
                             simulated_at: datetime | None = None) -> dict[str, Any]:
        """Create one durable paper execution receipt from an eligible observed quote.

        The deterministic simulation is behind the kernel's durable outbox. A missing or
        ambiguous receipt is never retried automatically; callers must reconcile UNKNOWN.
        """
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("paper execution is allowed only in an OPEN session")
        if not all(value.strip() for value in (order_id, decision_id, event_id)):
            raise ForwardSessionRunnerError("paper order and source decision identities are required")
        simulated_at = simulated_at or capture_local_time(self.clock, label="simulated execution timestamp")
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity < 1:
            raise ForwardSessionRunnerError("paper order side or quantity is invalid")
        if simulated_at.tzinfo is None or simulated_at.utcoffset() is None:
            raise ForwardSessionRunnerError("simulated execution clock must be timezone aware")
        if latency_ms < 0:
            raise ForwardSessionRunnerError("simulated latency cannot be negative")
        try:
            limit = Decimal(limit_price)
            tick = Decimal(tick_size)
            slippage = Decimal(adverse_slippage_bps)
        except Exception as exc:  # noqa: BLE001 - Decimal rejects malformed numeric inputs
            raise ForwardSessionRunnerError("limit, tick and slippage must be decimals") from exc
        if any(not value.is_finite() for value in (limit, tick, slippage)) or min(limit, tick) <= 0 or slippage < 0:
            raise ForwardSessionRunnerError("limit/tick must be positive and slippage non-negative")
        decision_row = self._con.execute("SELECT outcome, record_json FROM runner_decisions "
                                         "WHERE session_id=? AND decision_id=?",
                                         [session_id, decision_id]).fetchone()
        if decision_row is None or decision_row[0] != "TRADE":
            raise ForwardSessionRunnerError("paper intent requires a journaled TRADE decision")
        decision = json.loads(decision_row[1])
        risk = self._con.execute("SELECT allowed FROM runner_risk_verdicts "
                                  "WHERE session_id=? AND decision_id=?", [session_id, decision_id]).fetchone()
        if risk is None or risk[0] != 1:
            raise ForwardSessionRunnerError("paper intent requires an explicit persisted allow risk verdict")
        if event_id not in decision["input_event_ids"]:
            raise ForwardSessionRunnerError("paper quote must be an input to the recorded decision")
        event_row = self._con.execute("SELECT event_json FROM runner_events WHERE session_id=? AND event_id=?",
                                      [session_id, event_id]).fetchone()
        if event_row is None:
            raise ForwardSessionRunnerError("paper quote is absent from the session event journal")
        event = MarketEvent.from_dict(json.loads(event_row[0]))
        quote = executable_quote(event)
        if quote is None:
            raise ForwardSessionRunnerError("paper fill requires a two-sided quote with positive visible depth")
        if not event.evidence_id or re.fullmatch(r"[0-9a-f]{64}", event.evidence_id) is None:
            raise ForwardSessionRunnerError("paper fill requires an immutable SHA-256 source-payload evidence ID")
        accounting = self.records(session_id)["accounting"]
        if accounting["status"] != "RECONCILED_GROSS":
            raise ForwardSessionRunnerError("paper execution is blocked while the OMS/accounting projection is incomplete")
        instrument = event.instrument_id
        if side == "SELL" and int(accounting["positions"].get(instrument, 0)) < quantity:
            raise ForwardSessionRunnerError("paper sell exceeds reconciled long inventory")
        if side == "BUY" and Decimal(str(accounting["cash_inr"])) < limit * quantity:
            raise ForwardSessionRunnerError("paper buy exceeds reconciled available cash")
        decision_at = datetime.fromisoformat(decision["decided_at"])
        timed = create_timed_intent(decision_id=decision_id, event=event, side=side, quantity=quantity,
                                    decision_time=decision_at,
                                    latency=timedelta(milliseconds=latency_ms))
        if simulated_at.astimezone(timezone.utc) < timed.earliest_fill_time:
            raise ForwardSessionRunnerError("simulated fill precedes decision plus configured latency")
        touch = quote["ask"] if side == "BUY" else quote["bid"]
        visible = quote["ask_quantity"] if side == "BUY" else quote["bid_quantity"]
        request = {"session_id": session_id, "order_id": order_id, "decision_id": decision_id,
                   "event_id": event_id, "side": side, "quantity": quantity,
                   "limit_price": str(limit), "tick_size": str(tick), "freeze_limit": freeze_limit,
                   "slice_size": slice_size, "adverse_slippage_bps": str(slippage),
                   "latency_ms": latency_ms, "earliest_fill_time": timed.earliest_fill_time.isoformat(),
                   "simulated_at": simulated_at.astimezone(timezone.utc).isoformat()}
        request_json = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        request_sha = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        existing = self._con.execute("SELECT request_sha256 FROM paper_intents WHERE session_id=? AND order_id=?",
                                     [session_id, order_id]).fetchone()
        if existing and existing[0] != request_sha:
            raise ForwardSessionRunnerError("order ID reused with different paper intent")
        if not existing:
            self._con.execute("INSERT INTO paper_intents VALUES (?, ?, ?, ?, ?, ?)",
                              [session_id, order_id, decision_id, event_id, request_sha, request_json])
            self._append_audit("paper_intent", session_id, order_id, simulated_at, request)
            self._con.commit()

        def persist_simulation() -> None:
            scenario = evaluate_scenario(side=side, quantity=quantity, touch_price=touch,
                                         limit_price=limit, tick_size=tick, visible_quantity=visible,
                                         freeze_limit=freeze_limit, slice_size=slice_size,
                                         adverse_slippage_bps=slippage)
            body = {"session_id": session_id, "order_id": order_id, "status": scenario.status,
                    "requested_quantity": scenario.requested_quantity,
                    "filled_quantity": scenario.filled_quantity, "inventory_delta": scenario.inventory_delta,
                    "average_price": str(scenario.average_price) if scenario.average_price is not None else None,
                    "child_quantities": list(scenario.child_quantities), "reject_reason": scenario.reject_reason,
                    "adverse_slippage_bps": str(scenario.adverse_slippage_bps),
                    "quote_event_id": event_id, "source": event.source,
                    "simulated_at": simulated_at.astimezone(timezone.utc).isoformat(),
                    "physical_order_submitted": False}
            self._persist_paper_receipt(session_id, order_id, body)

        from financial_brain.kernel.events import EventKernel
        outbox_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        outbox = EventKernel(journal_path=str(outbox_path))
        try:
            outbox.side_effect(f"paper:{session_id}:{order_id}", persist_simulation)
        finally:
            outbox.close()
        receipt = self._con.execute("SELECT receipt_json FROM paper_execution_receipts "
                                    "WHERE session_id=? AND order_id=?", [session_id, order_id]).fetchone()
        if receipt is None:
            raise ForwardSessionRunnerError("outbox acknowledged without a persisted simulation receipt")
        return json.loads(receipt[0])

    def _persist_paper_receipt(self, session_id: str, order_id: str,
                               body: dict[str, Any]) -> None:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        previous = self._con.execute("SELECT receipt_sha256 FROM paper_execution_receipts "
                                     "WHERE session_id=? AND order_id=?", [session_id, order_id]).fetchone()
        if previous and previous[0] != digest:
            raise ForwardSessionRunnerError("paper execution receipt conflicts with existing result")
        if not previous:
            self._con.execute("INSERT INTO paper_execution_receipts VALUES (?, ?, ?, ?)",
                              [session_id, order_id, digest, canonical])
            self._append_audit("execution_receipt", session_id, order_id,
                               datetime.fromisoformat(body["simulated_at"]), body)
            self._con.commit()

    def reconcile_unknown_paper_intent(self, *, session_id: str, order_id: str,
                                       reconciled_by: str) -> bool:
        """Reconcile a crash-ambiguous local simulation against its durable receipt row."""
        if not reconciled_by.strip():
            raise ForwardSessionRunnerError("paper simulation reconciliation needs an actor")
        from financial_brain.kernel.events import EventKernel
        outbox_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        outbox = EventKernel(journal_path=str(outbox_path))
        key = f"paper:{session_id}:{order_id}"
        try:
            if key not in outbox.unknown_keys:
                raise ForwardSessionRunnerError("paper intent has no UNKNOWN outbox state")
            receipt = self._con.execute("SELECT receipt_sha256 FROM paper_execution_receipts "
                                        "WHERE session_id=? AND order_id=?", [session_id, order_id]).fetchone()
            outbox.resolve_unknown(key, took_effect=receipt is not None, reconciled_by=reconciled_by)
            return receipt is not None
        finally:
            outbox.close()

    def close_session(self, session_id: str, *, complete: bool, now: datetime | None = None,
                      reason: str | None = None) -> dict[str, Any]:
        """Close evidence with a deterministic digest; incomplete sessions remain incomplete."""
        now = now or capture_local_time(self.clock, label="session close")
        state = self.state_store.state(session_id)
        if state in {SessionState.CLOSED, SessionState.INCOMPLETE}:
            existing = self._con.execute("SELECT report_json FROM runner_session_reports WHERE session_id=?",
                                         [session_id]).fetchone()
            if existing:
                return json.loads(existing[0])
            raise ForwardSessionRunnerError("terminal session has no persisted close report")
        if state not in {SessionState.OPEN, SessionState.DRAINING}:
            raise ForwardSessionRunnerError("only an OPEN or DRAINING session can close")
        if complete:
            self._require_resolved_paper_intents(session_id)
            accounting = self.records(session_id)["accounting"]
            if accounting["status"] != "RECONCILED_GROSS" or not accounting["cash_reconciled"]:
                raise ForwardSessionRunnerError("cannot close complete with OMS/accounting reconciliation errors")
        if state is SessionState.OPEN:
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
                "intent_count": len(records["intents"]),
                "execution_receipt_count": len(records["execution_receipts"]),
                "reason": reason, "records_sha256": hashlib.sha256(json.dumps(
                    records, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        report = body | {"report_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
        report_json = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._con.execute("INSERT INTO runner_session_reports VALUES (?, ?, ?, ?)",
                          [session_id, status, report["report_sha256"], report_json])
        self._append_audit("session_report", session_id, session_id, now, report)
        self._con.commit()
        return report

    def _require_resolved_paper_intents(self, session_id: str) -> None:
        intents = self._con.execute("SELECT order_id FROM paper_intents WHERE session_id=?",
                                    [session_id]).fetchall()
        if not intents:
            return
        receipts = self._con.execute("SELECT order_id FROM paper_execution_receipts WHERE session_id=?",
                                     [session_id]).fetchall()
        if {row[0] for row in intents} != {row[0] for row in receipts}:
            raise ForwardSessionRunnerError("cannot close complete with missing paper execution receipts")
        outbox_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        if not outbox_path.is_file():
            raise ForwardSessionRunnerError("cannot close complete without durable outbox evidence")
        with sqlite3.connect(str(outbox_path)) as outbox:
            rows = outbox.execute("SELECT key, state FROM outbox WHERE key LIKE ?",
                                  [f"paper:{session_id}:%"]).fetchall()
        expected = {f"paper:{session_id}:{row[0]}" for row in intents}
        actual = {key for key, state in rows if state == "DISPATCHED"}
        if expected != actual:
            raise ForwardSessionRunnerError("cannot close complete with pending or UNKNOWN paper outbox state")

    def _append_record(self, kind: str, session_id: str, record: dict[str, Any]) -> None:
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        event_id = f"{session_id}:{kind}"
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        previous = self._con.execute("SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
                                     [session_id, event_id]).fetchone()
        if previous and previous[0] != digest:
            raise ForwardSessionRunnerError("session lifecycle record conflicts with prior evidence")
        if not previous:
            self._con.execute("""INSERT INTO runner_events (
                                session_id, event_id, kind, available_at, ingested_at,
                                ingest_clock_source, event_sha256, event_json)
                                VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)""",
                              [session_id, event_id, kind, record["occurred_at"], digest, canonical])
            self._append_audit(kind, session_id, event_id, datetime.fromisoformat(record["occurred_at"]), record)
            self._con.commit()

    def _append_audit(self, kind: str, session_id: str, record_id: str, occurred_at: datetime,
                      record: dict[str, Any]) -> None:
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        stable_id = f"{kind}:{record_id}"
        previous = self._con.execute(
            "SELECT record_sha256 FROM runner_audit_log WHERE session_id=? AND record_id=?",
            [session_id, stable_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise ForwardSessionRunnerError("append-only audit record conflicts with prior evidence")
            return
        self._con.execute("INSERT INTO runner_audit_log "
                          "(session_id, record_id, kind, occurred_at, record_sha256, record_json) "
                          "VALUES (?, ?, ?, ?, ?, ?)",
                          [session_id, stable_id, kind, occurred_at.astimezone(timezone.utc).isoformat(),
                           digest, canonical])

    def _record_data_rejection(self, session_id: str, operation: str, reason: str,
                               events: list[MarketEvent]) -> None:
        rejected_at = capture_local_time(self.clock, label="data rejection timestamp").astimezone(timezone.utc)
        source_ids = sorted(event.event_id for event in events)
        seed = json.dumps({"operation": operation, "reason": reason, "event_ids": source_ids},
                          sort_keys=True, separators=(",", ":"))
        rejection_id = hashlib.sha256(f"{seed}:{uuid.uuid4()}".encode("utf-8")).hexdigest()
        record = {"session_id": session_id, "rejection_id": rejection_id, "operation": operation,
                  "reason": reason, "source_event_ids": source_ids, "rejected_at": rejected_at.isoformat()}
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        previous = self._con.execute("SELECT record_sha256 FROM runner_data_rejections "
                                     "WHERE session_id=? AND rejection_id=?", [session_id, rejection_id]).fetchone()
        if previous:
            return
        self._con.execute("INSERT INTO runner_data_rejections VALUES (?, ?, ?, ?)",
                          [session_id, rejection_id, digest, canonical])
        self._append_audit("data_rejection", session_id, rejection_id, rejected_at, record)
        self._con.commit()

