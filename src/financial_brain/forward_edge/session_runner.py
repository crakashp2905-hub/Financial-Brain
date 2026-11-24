"""Durable normalized-event, manifest, risk and paper execution session runner."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import InvalidOperation, ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from financial_brain.evidence.decision_manifest import ManifestError, canonical_bytes
from financial_brain.forward_edge.execution_scenario import (
    ScenarioFill,
    evaluate_depth_scenario,
    evaluate_passive_scenario,
)
from financial_brain.forward_edge.durable import DurableJournalError, DurableSessionJournal
from financial_brain.forward_edge.data_permissions import DataPermission, DataUse
from financial_brain.forward_edge.instrument_rules import InstrumentRuleBook, InstrumentRuleError
from financial_brain.forward_edge.clock import ClockProvider, SystemClock, capture_local_time
from financial_brain.forward_edge.intent_timing import create_timed_intent
from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.order_journal import OrderEvent, OrderJournal, OrderJournalError
from financial_brain.forward_edge.provenance import repository_provenance
from financial_brain.forward_edge.quote_archive import QuoteArchive
from financial_brain.forward_edge.session_state import (
    ForwardRunConfig,
    ForwardSessionStore,
    SessionState,
)
from financial_brain.kernel.units import BasisPoints, Price, UnitError, WholeUnits
from financial_brain.forward_edge.session_ops import REQUIRED_PREFLIGHT
from financial_brain.forward_edge.venue_calendar import VenueCalendar

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MAX_QUOTE_AGE_MS = 5_000


class ForwardSessionRunnerError(ValueError):
    """Session runner rejected an event, decision or lifecycle operation."""


class ForwardSessionRunner:
    """Persist event and decision evidence from any adapter using normalized MarketEvent rows.

    This runner deliberately has no physical-order operation. It persists source events, canonical decision
    manifests, risk verdicts and simulated paper execution receipts; it does not claim independent timestamp
    custody or cost-complete net P&L.
    """

    def __init__(self, state: ForwardSessionStore, journal_path: Path, *,
                 clock: ClockProvider | None = None) -> None:
        self.state_store = state
        self.clock = clock or SystemClock()
        self.journal_path = Path(journal_path)
        self.provenance = repository_provenance(entry_point="ForwardSessionRunner")
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
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_decision_manifests (
            session_id TEXT NOT NULL, decision_id TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
            manifest_json TEXT NOT NULL, PRIMARY KEY(session_id, decision_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_data_rejections (
            session_id TEXT NOT NULL, rejection_id TEXT NOT NULL, record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL, PRIMARY KEY(session_id, rejection_id))""")
        self.durable = DurableSessionJournal(self._con)
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
        self.order_journal = OrderJournal(connection=self._con)
        self._con.commit()
        # Keep simulated execution acknowledgements beside the journal and receipt rows.
        # Existing per-run outbox files are imported once so a restart across this migration
        # does not forget a PENDING, DISPATCHED, or reconciled outcome.
        from financial_brain.kernel.events import EventKernel

        EventKernel(connection=self._con).close()
        self._con.execute("CREATE TABLE IF NOT EXISTS runner_migrations (migration_id TEXT PRIMARY KEY)")
        self._migrate_legacy_outbox()

    def _migrate_legacy_outbox(self) -> None:
        migration_id = "paper_outbox_to_session_journal_v1"
        done = self._con.execute("SELECT 1 FROM runner_migrations WHERE migration_id=?",
                                 [migration_id]).fetchone()
        legacy_path = self.journal_path.with_name(f"{self.journal_path.stem}-outbox.sqlite")
        if done or not legacy_path.is_file():
            return
        try:
            with sqlite3.connect(str(legacy_path)) as legacy:
                outbox_rows = legacy.execute(
                    "SELECT key, state, reconciled_by FROM outbox ORDER BY key").fetchall()
                reconciliation_rows = legacy.execute(
                    "SELECT key, took_effect, reconciled_by FROM reconciliations "
                    "ORDER BY reconciliation_id").fetchall()
        except sqlite3.Error as exc:
            raise ForwardSessionRunnerError("legacy paper outbox cannot be migrated") from exc
        try:
            self._con.execute("BEGIN IMMEDIATE")
            for row in outbox_rows:
                existing = self._con.execute("SELECT state, reconciled_by FROM outbox WHERE key=?",
                                             [row[0]]).fetchone()
                if existing is not None and tuple(existing) != tuple(row[1:]):
                    raise ForwardSessionRunnerError("legacy outbox conflicts with journal outbox state")
                self._con.execute("INSERT OR IGNORE INTO outbox (key,state,reconciled_by) VALUES (?,?,?)", row)
            for key, took_effect, actor in reconciliation_rows:
                present = self._con.execute(
                    "SELECT 1 FROM reconciliations WHERE key=? AND took_effect=? AND reconciled_by=?",
                    [key, took_effect, actor]).fetchone()
                if present is None:
                    self._con.execute("INSERT INTO reconciliations (key,took_effect,reconciled_by) VALUES (?,?,?)",
                                      [key, took_effect, actor])
            self._con.execute("INSERT INTO runner_migrations VALUES (?)", [migration_id])
            self._con.commit()
        except Exception:
            self._con.rollback()
            raise

    def _outbox_kernel(self):
        from financial_brain.kernel.events import EventKernel

        return EventKernel(connection=self._con)

    def _max_quote_age(self, session_id: str) -> int:
        value = self.state_store.config(session_id).constraints.get(
            "max_quote_age_ms", DEFAULT_MAX_QUOTE_AGE_MS)
        if type(value) is not int or value < 0:
            raise ForwardSessionRunnerError("max_quote_age_ms must be a non-negative integer in run constraints")
        return value

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
        if not isinstance(preflight, dict) or not preflight:
            raise ForwardSessionRunnerError("preflight must contain explicit checks")
        if any(not isinstance(name, str) or not name.strip() or type(passed) is not bool
               for name, passed in preflight.items()):
            raise ForwardSessionRunnerError("preflight checks need non-empty names and boolean results")
        # A caller may not omit a required gate and thereby turn absence into readiness.
        # Persist the complete fixed set, representing missing checks explicitly as false.
        checks = {**preflight, **{name: preflight.get(name) is True for name in REQUIRED_PREFLIGHT}}
        if state is SessionState.PLANNED:
            self.state_store.transition(session_id, SessionState.PREFLIGHT, now=now)
        elif state is not SessionState.PREFLIGHT:
            raise ForwardSessionRunnerError(f"cannot resume preflight from {state.value}")
        record = {"session_id": session_id, "event": "PREFLIGHT", "occurred_at": now.astimezone(timezone.utc).isoformat(),
                  "checks": dict(sorted(checks.items())),
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
        failed = sorted(name for name, passed in checks.items() if passed is not True)
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

    def resume(self, session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Reopen durable session context after a process restart without replaying side effects."""
        now = now or capture_local_time(self.clock, label="session resume")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ForwardSessionRunnerError("session resume clock must be timezone aware")
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
                self.close_session(session_id, complete=True, now=now)
            except ForwardSessionRunnerError as exc:
                # An unresolved or incomplete close becomes durable INCOMPLETE, never an inferred success.
                self.close_session(session_id, complete=False, now=now,
                                   reason=f"restart recovery could not attest a complete close: {exc}")
            state = self.state_store.state(session_id)
        elif state is SessionState.OPEN:
            plan = next((item for item in records["lifecycle"]
                         if item.get("event") == "CALENDAR_PLAN" and item.get("outcome") == "OPEN"), None)
            if plan and datetime.fromisoformat(plan["closes_at"]) <= now.astimezone(timezone.utc):
                self.close_session(
                    session_id, complete=False, now=now,
                    reason="process resumed after the sourced venue close without a durable session close",
                )
                state = self.state_store.state(session_id)

        unknown = [row[0] for row in self._con.execute(
            "SELECT key FROM outbox WHERE state IN ('PENDING','UNKNOWN') AND key LIKE ? ORDER BY key",
            [f"paper:{session_id}:%"]).fetchall()]
        key_prefix = f"paper:{session_id}:"
        for key in unknown:
            order_id = key[len(key_prefix):]
            intent = self._con.execute("SELECT request_json FROM paper_intents "
                                       "WHERE session_id=? AND order_id=?",
                                       [session_id, order_id]).fetchone()
            if intent is None:
                raise ForwardSessionRunnerError("unresolved paper outbox key has no durable intent")
            request = json.loads(intent[0])
            self._ensure_paper_order_submitted(
                session_id=session_id, order_id=order_id, quantity=int(request["quantity"]),
                occurred_at=datetime.fromisoformat(request["simulated_at"]))
            order_ref = f"{session_id}::{order_id}"
            history = self.order_journal.replay(order_ref)
            if history[-1]["state"] not in {"UNKNOWN", "TIMED_OUT"}:
                if history[-1]["state"] in {"FILLED", "CANCELED", "REJECTED", "MISSED", "EXPIRED"}:
                    raise ForwardSessionRunnerError("unknown outbox conflicts with terminal order evidence")
                self._append_paper_order_event(
                    session_id=session_id, order_id=order_id, state="UNKNOWN", occurred_at=now,
                    quantity=int(request["quantity"]), filled_quantity=0,
                    accounting={"source": "RESTARTED_DURABLE_OUTBOX", "outcome": "UNKNOWN"})
        if unknown:
            self._con.commit()
        dispatched = [row[0] for row in self._con.execute(
            "SELECT key FROM outbox WHERE state='DISPATCHED' AND key LIKE ? ORDER BY key",
            [f"paper:{session_id}:%"]).fetchall()]
        for key in dispatched:
            order_id = key[len(key_prefix):]
            receipt = self._con.execute("SELECT receipt_sha256, receipt_json FROM paper_execution_receipts "
                                        "WHERE session_id=? AND order_id=?", [session_id, order_id]).fetchone()
            if receipt is None:
                raise ForwardSessionRunnerError("dispatched paper outbox has no durable execution receipt")
            body = json.loads(receipt[1])
            order_ref = f"{session_id}::{order_id}"
            history = self.order_journal.replay(order_ref)
            if not history or history[-1]["state"] == "CREATED":
                intent = self._con.execute("SELECT request_json FROM paper_intents "
                                            "WHERE session_id=? AND order_id=?",
                                            [session_id, order_id]).fetchone()
                if intent is None:
                    raise ForwardSessionRunnerError("dispatched paper order has no durable intent")
                request = json.loads(intent[0])
                self._ensure_paper_order_submitted(
                    session_id=session_id, order_id=order_id, quantity=int(request["quantity"]),
                    occurred_at=datetime.fromisoformat(request["simulated_at"]))
                history = self.order_journal.replay(order_ref)
            if history[-1]["state"] in {"SUBMITTED", "ACCEPTED", "WORKING", "UNKNOWN", "TIMED_OUT"}:
                self._append_paper_receipt_outcome(
                    session_id=session_id, order_id=order_id, receipt=body, receipt_sha256=receipt[0],
                    occurred_at=datetime.fromisoformat(body["simulated_at"]),
                    reconciled_by="DURABLE_OUTBOX_DISPATCHED")
            elif (history[-1]["state"] != body["status"]
                  or history[-1]["filled_quantity"] != body["filled_quantity"]):
                raise ForwardSessionRunnerError("dispatched receipt conflicts with terminal order journal state")
        if dispatched:
            self._con.commit()
        records = self.records(session_id)
        return {"session_id": session_id, "state": state.value,
                "config_sha256": config.sha256(), "records": self.records(session_id),
                "unresolved_paper_outbox_keys": unknown,
                "manual_reconciliation_required": bool(unknown),
                "physical_orders_enabled": False}

    def supervise_open_sessions(self, *, now: datetime | None = None,
                                max_feed_silence: timedelta = timedelta(seconds=90)) -> list[dict[str, Any]]:
        """Sweep OPEN sessions and durably fail closed on missing calendar, stale feed, or missed close."""
        now = now or capture_local_time(self.clock, label="session supervisor clock")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ForwardSessionRunnerError("session supervisor clock must be timezone aware")
        if max_feed_silence <= timedelta(0):
            raise ForwardSessionRunnerError("maximum feed silence must be positive")
        results: list[dict[str, Any]] = []
        for session_id in self.state_store.session_ids():
            if self.state_store.state(session_id) is not SessionState.OPEN:
                continue
            plan_row = self._con.execute(
                "SELECT event_json FROM runner_events WHERE session_id=? AND kind='calendar_plan'",
                [session_id]).fetchone()
            reason: str | None = None
            stale_seconds: float | None = None
            if plan_row is None:
                reason = "supervisor cannot verify feed or close because sourced venue calendar plan is missing"
            else:
                plan = json.loads(plan_row[0])
                if plan.get("outcome") != "OPEN" or not plan.get("opens_at") or not plan.get("closes_at"):
                    reason = "supervisor found an OPEN state without supported sourced venue bounds"
                else:
                    utc_now = now.astimezone(timezone.utc)
                    opens_at = datetime.fromisoformat(plan["opens_at"])
                    closes_at = datetime.fromisoformat(plan["closes_at"])
                    if utc_now >= closes_at:
                        reason = "supervisor observed the sourced venue close without a durable session close"
                    elif utc_now >= opens_at:
                        latest = self._con.execute(
                            "SELECT MAX(available_at) FROM runner_events WHERE session_id=? "
                            "AND kind NOT IN ('preflight','calendar_plan')", [session_id]).fetchone()[0]
                        last_observed = datetime.fromisoformat(latest) if latest else opens_at
                        stale = utc_now - last_observed
                        stale_seconds = stale.total_seconds()
                        if stale < timedelta(0):
                            raise ForwardSessionRunnerError("supervisor clock precedes latest available market event")
                        if stale >= max_feed_silence:
                            reason = (f"market feed silent for {stale_seconds:.3f}s; "
                                      f"limit is {max_feed_silence.total_seconds():.3f}s")
            if reason is not None:
                report = self.close_session(session_id, complete=False, now=now, reason=reason)
                results.append({"session_id": session_id, "state": report["status"],
                                "reason": reason, "stale_seconds": stale_seconds})
            else:
                results.append({"session_id": session_id, "state": SessionState.OPEN.value,
                                "reason": None, "stale_seconds": stale_seconds})
        return results

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
                permission_evidence: dict[str, Any] | None = None
                if event.source in {"FIXTURE", "SYNTHETIC_FIXTURE"}:
                    if config.constraints.get("allow_synthetic_fixture_events") is not True:
                        raise ForwardSessionRunnerError(
                            "fixture market events require an explicit frozen synthetic-fixture session constraint")
                else:
                    permission_rows = config.constraints.get("data_permissions", [])
                    matching = [row for row in permission_rows if isinstance(row, dict)
                                and row.get("source_id") == event.source
                                and row.get("use") == DataUse.PAPER_SIMULATION.value]
                    if len(matching) != 1:
                        raise ForwardSessionRunnerError(
                            f"market data rights are unknown for source {event.source!r} and paper simulation")
                    try:
                        permission = DataPermission.from_dict(matching[0])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ForwardSessionRunnerError(f"invalid market data permission: {exc}") from exc
                    local_date = event.available_at.astimezone(IST).date()
                    if not permission.allows(source_id=event.source, use=DataUse.PAPER_SIMULATION, on=local_date):
                        raise ForwardSessionRunnerError(
                            f"market data permission does not allow paper simulation for {event.source!r}")
                    permission_evidence = dict(matching[0])
                if event.event_time.astimezone(IST).date() != config.exchange_date:
                    raise ForwardSessionRunnerError("event exchange date differs from frozen session date")
                if event.available_at < event.received_at:
                    raise ForwardSessionRunnerError("event availability cannot precede local receipt")
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
                                    "ingest_clock_source": ingest_source,
                                    "data_permission": permission_evidence,
                                    "provenance": self.provenance})
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
            f"SELECT event_id, kind, available_at FROM runner_events WHERE session_id=? "
            f"AND kind NOT IN ('preflight','calendar_plan') AND event_id IN ({','.join('?' for _ in unique_ids)})",
            [session_id, *unique_ids]).fetchall()
        if len(rows) != len(unique_ids):
            raise ForwardSessionRunnerError("decision references an event absent from this session")
        clock = decided_at.astimezone(timezone.utc)
        if any(datetime.fromisoformat(available) > clock for _, _, available in rows):
            raise ForwardSessionRunnerError("decision uses a source event unavailable at decision time")
        max_quote_age = self._max_quote_age(session_id)
        stale = [event_id for event_id, kind, available in rows
                 if kind == "quote" and clock - datetime.fromisoformat(available) >
                 timedelta(milliseconds=max_quote_age)]
        if stale:
            raise ForwardSessionRunnerError(
                f"decision quote exceeds configured maximum age of {max_quote_age}ms: {', '.join(sorted(stale))}")
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
        prior = self._con.execute("SELECT MAX(decided_at) FROM runner_decisions WHERE session_id=?",
                                  [session_id]).fetchone()[0]
        if prior is not None and clock < datetime.fromisoformat(prior):
            raise ForwardSessionRunnerError("decision clock cannot move backwards within a session")
        self._con.execute("INSERT INTO runner_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [session_id, decision_id, strategy_id, outcome, clock.isoformat(), digest, canonical])
        self._append_audit("decision", session_id, decision_id, clock,
                           {**body, "provenance": self.provenance})
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
        self._append_audit("risk_verdict", session_id, verdict_id, occurred,
                           {**record, "provenance": self.provenance})
        self._con.commit()
        return True

    def record_decision_manifest(self, *, session_id: str, decision_id: str,
                                 manifest: dict[str, Any]) -> str:
        """Bind a strict canonical decision manifest to its journaled source events."""
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("decision manifests are accepted only in an OPEN session")
        try:
            canonical = canonical_bytes(manifest)
        except ManifestError as exc:
            raise ForwardSessionRunnerError(f"invalid canonical decision manifest: {exc}") from exc
        digest = hashlib.sha256(canonical).hexdigest()
        config = self.state_store.config(session_id)
        decision_row = self._con.execute("SELECT record_json FROM runner_decisions "
                                          "WHERE session_id=? AND decision_id=?",
                                          [session_id, decision_id]).fetchone()
        if decision_row is None:
            raise ForwardSessionRunnerError("decision manifest requires a persisted decision")
        decision = json.loads(decision_row[0])
        decision_at = datetime.fromisoformat(decision["decided_at"].replace("Z", "+00:00"))
        manifest_at = datetime.fromisoformat(manifest["decision_at_utc"].replace("Z", "+00:00"))
        if manifest["decision_id"] != decision_id or manifest["day"] != config.exchange_date.isoformat():
            raise ForwardSessionRunnerError("decision manifest identity or exchange date mismatches session")
        if manifest_at.astimezone(timezone.utc) != decision_at.astimezone(timezone.utc):
            raise ForwardSessionRunnerError("decision manifest time differs from the durable decision")
        source_ids = {source["source_id"] for source in manifest["sources"]}
        if source_ids != set(decision["input_event_ids"]):
            raise ForwardSessionRunnerError("decision manifest sources must exactly match decision inputs")
        for source in manifest["sources"]:
            row = self._con.execute("SELECT event_sha256, event_json FROM runner_events "
                                    "WHERE session_id=? AND event_id=?",
                                    [session_id, source["source_id"]]).fetchone()
            if row is None or row[0] != source["sha256"]:
                raise ForwardSessionRunnerError("decision manifest source hash differs from journaled market event")
            event = json.loads(row[1])
            for manifest_key, event_key in (("published_at_utc", "event_time"),
                                            ("received_at_utc", "received_at"),
                                            ("available_at_utc", "available_at")):
                source_time = datetime.fromisoformat(source[manifest_key].replace("Z", "+00:00"))
                event_time = datetime.fromisoformat(event[event_key].replace("Z", "+00:00"))
                if source_time.astimezone(timezone.utc) != event_time.astimezone(timezone.utc):
                    raise ForwardSessionRunnerError("decision manifest source clock differs from market event")
        encoded = canonical.decode("utf-8")
        previous = self._con.execute("SELECT manifest_sha256 FROM runner_decision_manifests "
                                     "WHERE session_id=? AND decision_id=?", [session_id, decision_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise ForwardSessionRunnerError("decision manifest conflicts with immutable prior manifest")
            return digest
        self._con.execute("INSERT INTO runner_decision_manifests VALUES (?, ?, ?, ?)",
                          [session_id, decision_id, digest, encoded])
        self._append_audit("decision_manifest", session_id, decision_id, decision_at,
                           {"decision_id": decision_id, "manifest_sha256": digest,
                            "manifest": json.loads(encoded), "provenance": self.provenance})
        self._con.commit()
        return digest

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
        decision_manifests = [json.loads(row[0]) for row in self._con.execute(
            "SELECT manifest_json FROM runner_decision_manifests WHERE session_id=? ORDER BY decision_id",
            [session_id]).fetchall()]
        data_rejections = [json.loads(row[0]) for row in self._con.execute(
            "SELECT record_json FROM runner_data_rejections WHERE session_id=? ORDER BY rejection_id",
            [session_id]).fetchall()]
        audit_log = self.durable.audit_records(session_id)
        order_events: list[dict[str, Any]] = []
        prefix = f"{session_id}::"
        for journal_order_id in self.order_journal.order_ids():
            if journal_order_id.startswith(prefix):
                paper_order_id = journal_order_id[len(prefix):]
                order_events.extend(
                    event | {"session_id": session_id, "paper_order_id": paper_order_id}
                    for event in self.order_journal.replay(journal_order_id))
        return {"events": events, "decisions": decisions, "lifecycle": lifecycle,
                "intents": intents, "execution_receipts": receipts, "session_reports": reports,
                "order_events": order_events,
                "risk_verdicts": risk_verdicts, "decision_manifests": decision_manifests,
                "data_rejections": data_rejections,
                "audit_log": audit_log, "accounting": accounting}

    def _append_paper_order_event(self, *, session_id: str, order_id: str, state: str,
                                  occurred_at: datetime, quantity: int, filled_quantity: int,
                                  price: str | None = None, fill_evidence_id: str | None = None,
                                  reconciliation_evidence_id: str | None = None,
                                  accounting: dict[str, Any] | None = None) -> bool:
        """Append one canonical order lifecycle event in the runner's transaction journal."""
        journal_order_id = f"{session_id}::{order_id}"
        previous = self.order_journal.replay(journal_order_id)
        if previous and (previous[-1]["state"], previous[-1]["filled_quantity"], previous[-1]["price"]) == (
                state, filled_quantity, price):
            return False
        sequence = len(previous)
        event_id_basis = json.dumps({
            "order": journal_order_id, "sequence": sequence, "state": state,
            "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
            "quantity": quantity, "filled_quantity": filled_quantity, "price": price,
            "fill_evidence_id": fill_evidence_id,
            "reconciliation_evidence_id": reconciliation_evidence_id,
        }, sort_keys=True, separators=(",", ":"), allow_nan=False)
        event_id = hashlib.sha256(event_id_basis.encode("utf-8")).hexdigest()
        event = OrderEvent(
            journal_order_id, event_id, sequence, state, occurred_at, quantity, filled_quantity,
            price, accounting or {}, fill_evidence_id, reconciliation_evidence_id)
        try:
            return self.order_journal.append(event, commit=False)
        except OrderJournalError as exc:
            raise ForwardSessionRunnerError(f"paper order lifecycle rejected: {exc}") from exc

    def _ensure_paper_order_submitted(self, *, session_id: str, order_id: str,
                                      quantity: int, occurred_at: datetime) -> None:
        journal_order_id = f"{session_id}::{order_id}"
        history = self.order_journal.replay(journal_order_id)
        if not history:
            self._append_paper_order_event(
                session_id=session_id, order_id=order_id, state="CREATED", occurred_at=occurred_at,
                quantity=quantity, filled_quantity=0, accounting={"source": "PAPER_INTENT"})
            history = self.order_journal.replay(journal_order_id)
        if history[-1]["state"] == "CREATED":
            self._append_paper_order_event(
                session_id=session_id, order_id=order_id, state="SUBMITTED", occurred_at=occurred_at,
                quantity=quantity, filled_quantity=0, accounting={"source": "PAPER_SIMULATION_REQUEST"})
        self._con.commit()

    def _append_paper_receipt_outcome(self, *, session_id: str, order_id: str,
                                      receipt: dict[str, Any], receipt_sha256: str,
                                      occurred_at: datetime, reconciled_by: str | None = None) -> None:
        state = str(receipt["status"])
        if state not in {"FILLED", "PARTIAL", "MISSED", "REJECTED"}:
            raise ForwardSessionRunnerError("paper receipt has no canonical order outcome")
        journal_order_id = f"{session_id}::{order_id}"
        history = self.order_journal.replay(journal_order_id)
        previous_filled = int(history[-1]["filled_quantity"]) if history else 0
        filled = int(receipt["filled_quantity"])
        current_state = history[-1]["state"] if history else None
        if history:
            last_event_at = datetime.fromisoformat(history[-1]["occurred_at"])
            if occurred_at.astimezone(timezone.utc) < last_event_at:
                occurred_at = last_event_at
        reconciliation_id = None
        if current_state in {"UNKNOWN", "TIMED_OUT"}:
            basis = f"paper-order-resolution|{journal_order_id}|{reconciled_by or 'OUTBOX_DISPATCHED'}|{receipt_sha256}"
            reconciliation_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        self._append_paper_order_event(
            session_id=session_id, order_id=order_id, state=state, occurred_at=occurred_at,
            quantity=int(receipt["requested_quantity"]), filled_quantity=filled,
            price=receipt.get("average_price"),
            fill_evidence_id=receipt_sha256 if filled > previous_filled else None,
            reconciliation_evidence_id=reconciliation_id,
            accounting={"source": "PAPER_EXECUTION_RECEIPT", "receipt_sha256": receipt_sha256,
                        "resolution_actor": reconciled_by, "physical_order_submitted": False})
        self._con.commit()

    def source_event(self, session_id: str, event_id: str) -> tuple[MarketEvent, str]:
        """Return one normalized journal event and its immutable row digest."""
        row = self._con.execute("SELECT event_json, event_sha256 FROM runner_events "
                                "WHERE session_id=? AND event_id=?", [session_id, event_id]).fetchone()
        if row is None:
            raise ForwardSessionRunnerError("source event is absent from the session journal")
        return MarketEvent.from_dict(json.loads(row[0])), str(row[1])

    def rebuild_materialized_views(self, session_id: str) -> None:
        """Rebuild query tables for a day from the immutable append-only audit log."""
        audit = [(row["kind"], json.dumps(row["record"], sort_keys=True, separators=(",", ":"),
                                           allow_nan=False))
                 for row in self.durable.audit_records(session_id)]
        if not audit:
            raise ForwardSessionRunnerError("cannot rebuild session views without canonical audit events")
        self._con.commit()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            for table in ("runner_events", "runner_decisions", "runner_risk_verdicts",
                          "runner_decision_manifests",
                          "runner_data_rejections", "paper_intents", "paper_execution_receipts",
                          "runner_session_reports"):
                self._con.execute(f"DELETE FROM {table} WHERE session_id=?", [session_id])
            for kind, encoded in audit:
                body = json.loads(encoded)
                # Provenance belongs to the immutable audit envelope. Materialized rows keep
                # their original contracts so replay/rebuild remains byte-equivalent.
                materialized = {key: value for key, value in body.items() if key != "provenance"}
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
                    encoded_body = json.dumps(materialized, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    self._con.execute("INSERT INTO runner_decisions VALUES (?,?,?,?,?,?,?)",
                                      [session_id, materialized["decision_id"], materialized["strategy_id"],
                                       materialized["outcome"], materialized["decided_at"],
                                       self._sha256(materialized), encoded_body])
                elif kind == "risk_verdict":
                    self._con.execute("INSERT INTO runner_risk_verdicts VALUES (?,?,?,?,?,?)",
                                      [session_id, materialized["decision_id"], materialized["verdict_id"],
                                       int(materialized["allowed"]), self._sha256(materialized),
                                       json.dumps(materialized, sort_keys=True, separators=(",", ":"), allow_nan=False)])
                elif kind == "decision_manifest":
                    manifest_json = json.dumps(body["manifest"], sort_keys=True,
                                               separators=(",", ":"), allow_nan=False)
                    self._con.execute("INSERT INTO runner_decision_manifests VALUES (?,?,?,?)",
                                      [session_id, body["decision_id"], body["manifest_sha256"], manifest_json])
                elif kind == "data_rejection":
                    self._con.execute("INSERT INTO runner_data_rejections VALUES (?,?,?,?)",
                                      [session_id, body["rejection_id"], self._sha256(body), encoded])
                elif kind == "paper_intent":
                    request = materialized
                    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    request_hash = self._sha256(request)
                    self._con.execute("INSERT INTO paper_intents VALUES (?,?,?,?,?,?)",
                                      [session_id, request["order_id"], request["decision_id"],
                                       request["event_id"], request_hash, request_json])
                elif kind == "execution_receipt":
                    encoded_body = json.dumps(materialized, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    self._con.execute("INSERT INTO paper_execution_receipts VALUES (?,?,?,?)",
                                      [session_id, materialized["order_id"], self._sha256(materialized), encoded_body])
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
        account_sessions: list[tuple[str, ForwardRunConfig]] = []
        for candidate_id in self.state_store.session_ids():
            candidate = self.state_store.config(candidate_id)
            if candidate.ledger_id != config.ledger_id:
                continue
            # Later trading dates must not change an earlier session's frozen accounting.
            if candidate.exchange_date < config.exchange_date or candidate_id == session_id:
                account_sessions.append((candidate_id, candidate))
        account_sessions.sort(key=lambda row: (row[1].exchange_date, row[0]))
        if any(candidate.capital != config.capital for _, candidate in account_sessions):
            raise ForwardSessionRunnerError("sessions sharing a paper ledger must use identical opening capital")
        if len(account_sessions) > 1:
            all_events: list[dict[str, Any]] = []
            all_intents: list[dict[str, Any]] = []
            all_receipts: list[dict[str, Any]] = []
            for candidate_id, _ in account_sessions:
                for (encoded,) in self._con.execute(
                        "SELECT event_json FROM runner_events WHERE session_id=? "
                        "AND kind NOT IN ('preflight','calendar_plan') ORDER BY available_at,event_id",
                        [candidate_id]).fetchall():
                    event = json.loads(encoded)
                    event["accounting_event_id"] = f"{candidate_id}:{event['event_id']}"
                    all_events.append(event)
                for (encoded,) in self._con.execute(
                        "SELECT request_json FROM paper_intents WHERE session_id=? ORDER BY rowid",
                        [candidate_id]).fetchall():
                    intent = json.loads(encoded)
                    intent["accounting_order_id"] = f"{candidate_id}:{intent['order_id']}"
                    intent["accounting_event_id"] = f"{candidate_id}:{intent['event_id']}"
                    all_intents.append(intent)
                for (encoded,) in self._con.execute(
                        "SELECT receipt_json FROM paper_execution_receipts WHERE session_id=? ORDER BY rowid",
                        [candidate_id]).fetchall():
                    receipt = json.loads(encoded)
                    receipt["accounting_order_id"] = f"{candidate_id}:{receipt['order_id']}"
                    all_receipts.append(receipt)
            events, intents, receipts = all_events, all_intents, all_receipts
        return rebuild_paper_accounting(account_id=config.ledger_id, capital=Decimal(config.capital),
                                        events=events, intents=intents, receipts=receipts)

    def checkpoint(self, session_id: str, *, phase: str, now: datetime | None = None) -> dict[str, Any]:
        """Atomically freeze one START or END journal, outbox, and accounting snapshot."""
        phase = phase.strip().upper()
        if phase not in {"START", "END"}:
            raise ForwardSessionRunnerError("checkpoint phase must be START or END")
        now = now or capture_local_time(self.clock, label="checkpoint timestamp")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ForwardSessionRunnerError("checkpoint timestamp must be timezone aware")
        checkpoint_id = f"{session_id}:{phase.lower()}"
        self._con.execute("BEGIN IMMEDIATE")
        try:
            existing = self._con.execute("SELECT checkpoint_sha256, checkpoint_json FROM runner_checkpoints "
                                         "WHERE session_id=? AND checkpoint_id=?",
                                         [session_id, checkpoint_id]).fetchone()
            if existing and phase == "START":
                result = json.loads(existing[1]) | {"checkpoint_sha256": existing[0]}
                self._con.commit()
                return result
            state = self.state_store.state(session_id)
            records = self.records(session_id)
            if phase == "START" and any(
                    records[name] for name in ("events", "decisions", "intents", "execution_receipts")):
                raise ForwardSessionRunnerError("START checkpoint must precede market events and decisions")
            if phase == "END" and state not in {
                    SessionState.CLOSED, SessionState.NO_TRADE, SessionState.INCOMPLETE}:
                raise ForwardSessionRunnerError("END checkpoint requires a terminal session state")
            outbox_rows = [{"key": row[0], "state": row[1], "reconciled_by": row[2]}
                           for row in self._con.execute(
                               "SELECT key, state, reconciled_by FROM outbox "
                               "WHERE key LIKE ? ORDER BY key", [f"paper:{session_id}:%"]).fetchall()]
            hashes = {name: self._sha256(records[name])
                      for name in ("events", "decisions", "decision_manifests", "lifecycle", "intents",
                                   "execution_receipts")}
            hashes["accounting"] = self._sha256(records["accounting"])
            hashes["ledger"] = str(records["accounting"]["ledger_sha256"])
            hashes["outbox"] = self._sha256(outbox_rows)
            if existing:
                prior = json.loads(existing[1])
                if prior.get("session_state") != state.value or prior.get("record_hashes") != hashes:
                    raise ForwardSessionRunnerError(
                        "END checkpoint ID already contains different immutable evidence")
                self._con.commit()
                return prior | {"checkpoint_sha256": existing[0]}
            unresolved = any(row["state"] in {"PENDING", "UNKNOWN"} for row in outbox_rows)
            config = self.state_store.config(session_id)
            accounting = records["accounting"]
            costs_bound = (config.constraints.get("cost_schedule") is None or
                           accounting["filled_order_count"] == 0 or
                           accounting["status"] == "RECONCILED_NET")
            ledger_bound = accounting["status"] in {"RECONCILED_GROSS", "RECONCILED_NET"} \
                and not unresolved and costs_bound
            body = {"schema": "fb.forward-session-checkpoint.v1", "session_id": session_id,
                    "checkpoint_id": checkpoint_id, "phase": phase,
                    "captured_at": now.astimezone(timezone.utc).isoformat(),
                    "session_state": state.value, "record_hashes": hashes,
                    "ledger_sha256": records["accounting"]["ledger_sha256"],
                    "ledger_status": records["accounting"]["status"],
                    "atomic_with_ledger": ledger_bound}
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            self._con.execute("INSERT INTO runner_checkpoints VALUES (?, ?, ?, ?, ?)",
                              [session_id, checkpoint_id, phase, digest, canonical])
            self._con.commit()
            return body | {"checkpoint_sha256": digest}
        except Exception:
            self._con.rollback()
            raise

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
                  for name in ("events", "decisions", "decision_manifests", "lifecycle", "intents",
                               "execution_receipts")}
        hashes["accounting"] = self._sha256(records["accounting"])
        hashes["ledger"] = str(records["accounting"]["ledger_sha256"])
        outbox_rows = [{"key": row[0], "state": row[1], "reconciled_by": row[2]}
                       for row in self._con.execute(
                           "SELECT key, state, reconciled_by FROM outbox "
                           "WHERE key LIKE ? ORDER BY key", [f"paper:{session_id}:%"]).fetchall()]
        hashes["outbox"] = self._sha256(outbox_rows)
        return hashes

    @staticmethod
    def _sha256(value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def execute_paper_intent(self, *, session_id: str, order_id: str, decision_id: str,
                             event_id: str, side: str, quantity: int | WholeUnits,
                             limit_price: Decimal | Price, tick_size: Decimal | Price,
                             freeze_limit: int, slice_size: int,
                             adverse_slippage_bps: Decimal | BasisPoints, latency_ms: int,
                             quantity_unit: str | None = None,
                             simulated_at: datetime | None = None, max_wait_ms: int | None = None,
                             price_band: tuple[Decimal | Price, Decimal | Price] | None = None,
                             passive_trade_event_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        """Create one durable paper execution receipt from an eligible observed quote.

        The deterministic simulation is behind the kernel's durable outbox. A missing or
        ambiguous receipt is never retried automatically; callers must reconcile UNKNOWN.
        """
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("paper execution is allowed only in an OPEN session")
        if not all(value.strip() for value in (order_id, decision_id, event_id)):
            raise ForwardSessionRunnerError("paper order and source decision identities are required")
        simulated_at = simulated_at or capture_local_time(self.clock, label="simulated execution timestamp")
        try:
            quantity_value = quantity.units if isinstance(quantity, WholeUnits) else quantity
            if isinstance(quantity_value, Decimal):
                if quantity_value != quantity_value.to_integral_value():
                    raise ForwardSessionRunnerError("paper order quantity must be whole units")
                quantity_value = int(quantity_value)
            for label, value in (("limit price", limit_price), ("tick size", tick_size)):
                if isinstance(value, Price) and value.currency != "INR":
                    raise ForwardSessionRunnerError(f"paper {label} must be denominated in INR")
            if price_band is not None and any(
                isinstance(value, Price) and value.currency != "INR" for value in price_band
            ):
                raise ForwardSessionRunnerError("paper price band must be denominated in INR")
            limit_value = limit_price.value if isinstance(limit_price, Price) else limit_price
            tick_value = tick_size.value if isinstance(tick_size, Price) else tick_size
            slippage_value = (adverse_slippage_bps.value
                              if isinstance(adverse_slippage_bps, BasisPoints)
                              else adverse_slippage_bps)
            band_values = tuple(
                value.value if isinstance(value, Price) else value for value in price_band
            ) if price_band is not None else None
        except UnitError as exc:
            raise ForwardSessionRunnerError(str(exc)) from exc
        if side not in {"BUY", "SELL"} or type(quantity_value) is not int or quantity_value < 1:
            raise ForwardSessionRunnerError("paper order side or quantity is invalid")
        quantity = quantity_value
        if simulated_at.tzinfo is None or simulated_at.utcoffset() is None:
            raise ForwardSessionRunnerError("simulated execution clock must be timezone aware")
        if latency_ms < 0:
            raise ForwardSessionRunnerError("simulated latency cannot be negative")
        if max_wait_ms is not None and (type(max_wait_ms) is not int or max_wait_ms < 0):
            raise ForwardSessionRunnerError("max_wait_ms must be a non-negative integer when configured")
        if len(set(passive_trade_event_ids)) != len(passive_trade_event_ids):
            raise ForwardSessionRunnerError("passive trade event IDs must be unique")
        try:
            limit = Decimal(limit_value)
            tick = Decimal(tick_value)
            slippage = Decimal(slippage_value)
            band = tuple(Decimal(value) for value in band_values) if band_values is not None else None
        except Exception as exc:  # noqa: BLE001 - Decimal rejects malformed numeric inputs
            raise ForwardSessionRunnerError("limit, tick, slippage and price band must be decimals") from exc
        if any(not value.is_finite() for value in (limit, tick, slippage)) or min(limit, tick) <= 0 or slippage < 0:
            raise ForwardSessionRunnerError("limit/tick must be positive and slippage non-negative")
        if band is not None and (len(band) != 2 or not all(value.is_finite() for value in band)
                                 or band[0] <= 0 or band[0] > band[1]):
            raise ForwardSessionRunnerError("price band must contain ordered positive finite bounds")
        decision_row = self._con.execute("SELECT outcome, record_json FROM runner_decisions "
                                         "WHERE session_id=? AND decision_id=?",
                                         [session_id, decision_id]).fetchone()
        if decision_row is None or decision_row[0] != "TRADE":
            raise ForwardSessionRunnerError("paper intent requires a journaled TRADE decision")
        decision = json.loads(decision_row[1])
        candidate_intent = decision.get("payload", {}).get("candidate_intent")
        if candidate_intent is not None:
            if not isinstance(candidate_intent, dict):
                raise ForwardSessionRunnerError("canonical candidate intent must be an object")
            try:
                candidate_reference_price = Decimal(str(candidate_intent.get("reference_price", "0")))
            except InvalidOperation as exc:
                raise ForwardSessionRunnerError("canonical candidate reference price is invalid") from exc
            if (candidate_intent.get("intent_id") != order_id
                    or candidate_intent.get("decision_id") != decision_id
                    or candidate_intent.get("side") != side
                    or candidate_intent.get("quantity") != quantity
                    or candidate_intent.get("quantity_unit") != quantity_unit
                    or candidate_reference_price != limit):
                raise ForwardSessionRunnerError("paper order differs from its canonical candidate intent")
        risk = self._con.execute("SELECT allowed, record_json FROM runner_risk_verdicts "
                                  "WHERE session_id=? AND decision_id=?", [session_id, decision_id]).fetchone()
        if risk is None or risk[0] != 1:
            raise ForwardSessionRunnerError("paper intent requires an explicit persisted allow risk verdict")
        risk_record = json.loads(risk[1])
        if simulated_at.astimezone(timezone.utc) < datetime.fromisoformat(risk_record["assessed_at"]):
            raise ForwardSessionRunnerError("simulated fill cannot precede the persisted risk assessment")
        manifest_row = self._con.execute("SELECT manifest_sha256, manifest_json FROM runner_decision_manifests "
                                         "WHERE session_id=? AND decision_id=?",
                                         [session_id, decision_id]).fetchone()
        if manifest_row is None:
            raise ForwardSessionRunnerError("paper intent requires a canonical persisted decision manifest")
        manifest = json.loads(manifest_row[1])
        config = self.state_store.config(session_id)
        if config.constraints.get("cost_schedule") is not None:
            if config.constraints.get("liquidity_bucket") not in {"mega", "large", "mid", "small", "micro"}:
                raise ForwardSessionRunnerError(
                    "a configured cost schedule requires an explicit valid liquidity bucket")
            schedule = self._resolved_cost_schedule(session_id)
            if manifest.get("cost_model_sha256") != schedule.digest():
                raise ForwardSessionRunnerError(
                    "decision manifest cost-model hash differs from the run's versioned cost schedule")
        event_row = self._con.execute("SELECT event_json FROM runner_events WHERE session_id=? AND event_id=?",
                                      [session_id, event_id]).fetchone()
        if event_row is None:
            raise ForwardSessionRunnerError("paper quote is absent from the session event journal")
        event = MarketEvent.from_dict(json.loads(event_row[0]))
        if candidate_intent is not None and candidate_intent.get("symbol") != event.instrument_id:
            raise ForwardSessionRunnerError("paper quote instrument differs from the canonical candidate intent")
        if event_id in decision["input_event_ids"]:
            signal_event = event
        else:
            input_rows = self._con.execute(
                f"SELECT event_json FROM runner_events WHERE session_id=? AND event_id IN "
                f"({','.join('?' for _ in decision['input_event_ids'])}) ORDER BY available_at, event_id",
                [session_id, *decision["input_event_ids"]]).fetchall()
            signal_events = [MarketEvent.from_dict(json.loads(row[0])) for row in input_rows]
            matching = [candidate for candidate in signal_events
                        if candidate.instrument_id == event.instrument_id]
            signal_event = (matching or signal_events)[0]
        if event.available_at > simulated_at.astimezone(timezone.utc):
            raise ForwardSessionRunnerError("paper fill quote was unavailable at simulated execution time")
        quote_age = simulated_at.astimezone(timezone.utc) - event.available_at
        max_quote_age = self._max_quote_age(session_id)
        if quote_age > timedelta(milliseconds=max_quote_age):
            raise ForwardSessionRunnerError(
                f"paper fill quote exceeds configured maximum age of {max_quote_age}ms")
        selected = next((row for row in manifest["selections"]
                         if row["symbol"] == event.instrument_id), None)
        if selected is None or quantity > selected["quantity"]:
            raise ForwardSessionRunnerError("paper intent exceeds the manifest's selected instrument quantity")
        rule_data = config.constraints.get("instrument_rules")
        if not isinstance(rule_data, dict):
            raise ForwardSessionRunnerError("paper execution requires a versioned instrument rule catalogue")
        phase = event.payload.get("session_phase", config.constraints.get("session_phase"))
        if not isinstance(phase, str) or not phase.strip():
            raise ForwardSessionRunnerError("paper execution requires an explicit instrument session phase")
        try:
            rulebook = InstrumentRuleBook(rule_data)
            rule = rulebook.validate_order(
                instrument=event.instrument_id, day=config.exchange_date, session_phase=phase,
                quantity=quantity, limit_price=limit, tick_size=tick, quantity_unit=quantity_unit)
        except InstrumentRuleError as exc:
            raise ForwardSessionRunnerError(str(exc)) from exc
        if freeze_limit > rule.freeze_quantity:
            raise ForwardSessionRunnerError("requested freeze limit exceeds the effective instrument rule")
        quantity_unit = rule.quantity_unit
        quote = executable_quote(event)
        if quote is None:
            raise ForwardSessionRunnerError("paper fill requires a two-sided quote with positive visible depth")
        if not event.evidence_id or re.fullmatch(r"[0-9a-f]{64}", event.evidence_id) is None:
            raise ForwardSessionRunnerError("paper fill requires an immutable SHA-256 source-payload evidence ID")
        accounting = self.records(session_id)["accounting"]
        safely_reconciled_retry = (
            accounting["status"] == "INCOMPLETE"
            and not accounting["errors"]
            and accounting["unresolved_order_ids"] == [order_id]
            and self._paper_retry_reconciled_without_effect(session_id, order_id)
        )
        if accounting["status"] not in {"RECONCILED_GROSS", "RECONCILED_NET"} and not safely_reconciled_retry:
            raise ForwardSessionRunnerError("paper execution is blocked while the OMS/accounting projection is incomplete")
        instrument = event.instrument_id
        if side == "SELL" and int(accounting["positions"].get(instrument, 0)) < quantity:
            raise ForwardSessionRunnerError("paper sell exceeds reconciled long inventory")
        if side == "BUY" and Decimal(str(accounting["cash_inr"])) < limit * quantity:
            raise ForwardSessionRunnerError("paper buy exceeds reconciled available cash")
        decision_at = datetime.fromisoformat(decision["decided_at"])
        timed = create_timed_intent(decision_id=decision_id, event=signal_event, side=side, quantity=quantity,
                                    decision_time=decision_at,
                                    latency=timedelta(milliseconds=latency_ms))
        if simulated_at.astimezone(timezone.utc) < timed.earliest_fill_time:
            raise ForwardSessionRunnerError("simulated fill precedes decision plus configured latency")
        if event.available_at < timed.earliest_fill_time:
            raise ForwardSessionRunnerError("paper fill quote predates order eligibility after modeled latency")
        passive_events: list[MarketEvent] = []
        if passive_trade_event_ids:
            rows = self._con.execute(
                f"SELECT event_json FROM runner_events WHERE session_id=? AND event_id IN "
                f"({','.join('?' for _ in passive_trade_event_ids)})",
                [session_id, *passive_trade_event_ids]).fetchall()
            if len(rows) != len(passive_trade_event_ids):
                raise ForwardSessionRunnerError("passive fill references an unjournaled trade print")
            passive_events = [MarketEvent.from_dict(json.loads(row[0])) for row in rows]
            if any(trade.kind is not EventKind.TICK or trade.instrument_id != event.instrument_id
                   for trade in passive_events):
                raise ForwardSessionRunnerError("passive fills require same-instrument journaled trade ticks")
            if any(type(trade.payload.get("quantity")) is not int or trade.payload["quantity"] < 1
                   for trade in passive_events):
                raise ForwardSessionRunnerError("passive trade prints need positive integral quantities")
            if any(trade.available_at < timed.earliest_fill_time or trade.available_at >
                   simulated_at.astimezone(timezone.utc) for trade in passive_events):
                raise ForwardSessionRunnerError("passive trade print was outside the eligible execution window")
            if any(not trade.evidence_id or re.fullmatch(r"[0-9a-f]{64}", trade.evidence_id) is None
                   for trade in passive_events):
                raise ForwardSessionRunnerError("passive trade print requires immutable payload evidence")
        opposing_levels = quote["ask_levels"] if side == "BUY" else quote["bid_levels"]
        effective_rule = {"effective_from": rule.effective_from.isoformat(),
                          "tick_size": str(rule.tick_size), "lot_size": rule.lot_size,
                          "quantity_unit": rule.quantity_unit,
                          "freeze_quantity": rule.freeze_quantity, "expiry":
                          rule.expiry.isoformat() if rule.expiry else None,
                          "status": rule.status, "session_phases": sorted(rule.session_phases)}
        request = {"session_id": session_id, "order_id": order_id, "decision_id": decision_id,
                   "event_id": event_id, "side": side, "quantity": quantity,
                   "quantity_unit": quantity_unit,
                   "decision_manifest_sha256": manifest_row[0],
                   "instrument_rulebook_sha256": rulebook.digest,
                   "effective_instrument_rule": effective_rule,
                   "limit_price": str(limit), "tick_size": str(tick), "freeze_limit": freeze_limit,
                   "slice_size": slice_size, "adverse_slippage_bps": str(slippage),
                   "max_wait_ms": max_wait_ms,
                   "price_band": [str(value) for value in band] if band is not None else None,
                   "execution_style": "PASSIVE_LIMIT" if passive_trade_event_ids else "MARKETABLE_LIMIT",
                   "passive_trade_event_ids": list(passive_trade_event_ids),
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
            self._append_audit("paper_intent", session_id, order_id, simulated_at,
                               {**request, "provenance": self.provenance})
            self._con.commit()
        self._ensure_paper_order_submitted(session_id=session_id, order_id=order_id,
                                           quantity=quantity, occurred_at=simulated_at)

        def persist_simulation() -> None:
            deadline = (timed.earliest_fill_time + timedelta(milliseconds=max_wait_ms)
                        if max_wait_ms is not None else None)
            if deadline is not None and event.available_at > deadline:
                scenario = ScenarioFill("MISSED", quantity, 0, 0, None, (),
                                        "FIRST_QUOTE_AFTER_MAX_WAIT", slippage)
            elif passive_trade_event_ids:
                prints = tuple((Decimal(str(trade.payload["price"])), int(trade.payload.get("quantity", 0)))
                               for trade in sorted(passive_events, key=lambda row: row.available_at))
                scenario = evaluate_passive_scenario(
                    side=side, quantity=quantity, limit_price=limit, tick_size=tick,
                    freeze_limit=freeze_limit, slice_size=slice_size, adverse_slippage_bps=slippage,
                    trade_prints=prints, price_band=band)
            else:
                scenario = evaluate_depth_scenario(
                    side=side, quantity=quantity, levels=opposing_levels,
                    limit_price=limit, tick_size=tick, freeze_limit=freeze_limit,
                    slice_size=slice_size, adverse_slippage_bps=slippage,
                    price_band=band)
            body = {"session_id": session_id, "order_id": order_id, "status": scenario.status,
                    "requested_quantity": scenario.requested_quantity,
                    "quantity_unit": quantity_unit,
                    "filled_quantity": scenario.filled_quantity, "inventory_delta": scenario.inventory_delta,
                    "average_price": str(scenario.average_price) if scenario.average_price is not None else None,
                    "child_quantities": list(scenario.child_quantities), "reject_reason": scenario.reject_reason,
                    "adverse_slippage_bps": str(scenario.adverse_slippage_bps),
                    "instrument_rulebook_sha256": rulebook.digest,
                    "effective_instrument_rule": effective_rule,
                    "quote_event_id": event_id, "source": event.source,
                    "simulation_fidelity": "OBSERVED_TRADE_SIM" if passive_trade_event_ids
                        else "OBSERVED_QUOTE_SIM",
                    "quote_provenance": {
                        "event_id": event.event_id, "event_sha256": event.sha256(),
                        "event_kind": event.kind.value, "exchange": event.venue,
                        "source": event.source, "evidence_id": event.evidence_id,
                        "event_time_utc": event.event_time.isoformat(),
                        "received_at_utc": event.received_at.isoformat(),
                        "available_at_utc": event.available_at.isoformat(),
                    },
                    "trade_provenance": [{
                        "event_id": trade.event_id, "event_sha256": trade.sha256(),
                        "exchange": trade.venue, "source": trade.source,
                        "evidence_id": trade.evidence_id,
                        "event_time_utc": trade.event_time.isoformat(),
                        "received_at_utc": trade.received_at.isoformat(),
                        "available_at_utc": trade.available_at.isoformat(),
                    } for trade in passive_events],
                    "execution_style": request["execution_style"],
                    "execution_evidence_event_ids": [event_id, *passive_trade_event_ids],
                    "depth_match_mode": "PASSIVE_TRADE_PRINTS" if passive_trade_event_ids
                        else "OBSERVED_OPPOSING_BOOK_LEVELS",
                    "visible_depth_fills": [] if passive_trade_event_ids else [
                        {"price": str(price), "quantity": level_quantity}
                        for price, level_quantity in scenario.level_fills],
                    "observed_opposing_depth_quantity": None if passive_trade_event_ids else sum(
                        level_quantity for _price, level_quantity in opposing_levels),
                    "max_wait_ms": max_wait_ms,
                    "simulated_at": simulated_at.astimezone(timezone.utc).isoformat(),
                    "physical_order_submitted": False}
            if scenario.filled_quantity:
                self._attach_cost_evidence(session_id, request, body, price=scenario.average_price,
                                           filled_quantity=scenario.filled_quantity)
            self._persist_paper_receipt(session_id, order_id, body, commit=False)

        outbox = self._outbox_kernel()
        from financial_brain.kernel.events import UnknownOutcome

        try:
            try:
                outbox.side_effect(f"paper:{session_id}:{order_id}", persist_simulation)
            except UnknownOutcome:
                self._append_paper_order_event(
                    session_id=session_id, order_id=order_id, state="UNKNOWN",
                    occurred_at=simulated_at, quantity=quantity, filled_quantity=0,
                    accounting={"source": "DURABLE_OUTBOX", "outcome": "UNKNOWN"})
                self._con.commit()
                raise
        finally:
            outbox.close()
        receipt_row = self._con.execute("SELECT receipt_json, receipt_sha256 FROM paper_execution_receipts "
                                    "WHERE session_id=? AND order_id=?", [session_id, order_id]).fetchone()
        if receipt_row is None:
            raise ForwardSessionRunnerError("outbox acknowledged without a persisted simulation receipt")
        receipt = json.loads(receipt_row[0])
        self._append_paper_receipt_outcome(
            session_id=session_id, order_id=order_id, receipt=receipt, receipt_sha256=receipt_row[1],
            occurred_at=datetime.fromisoformat(receipt["simulated_at"]))
        return receipt

    def _attach_cost_evidence(self, session_id: str, request: dict[str, Any], receipt: dict[str, Any], *,
                              price: Decimal | None, filled_quantity: int) -> None:
        """Attach explicitly configured, dated cost line items to a simulated fill.

        Cost assumptions are opt-in run constraints. An absent schedule or liquidity bucket
        leaves the session gross-only; it never silently substitutes a default estimate.
        """
        from financial_brain.costs.india import Side

        config = self.state_store.config(session_id)
        bucket = config.constraints.get("liquidity_bucket")
        passive = request.get("execution_style") == "PASSIVE_LIMIT"
        if config.constraints.get("cost_schedule") is None:
            receipt["simulated_adverse_selection_cost_inr"] = self._passive_adverse_cost(
                session_id, request, price, filled_quantity) if passive else "0.00"
            receipt["execution_cost_basis"] = ("PASSIVE_TRADE_THROUGH_WITH_SEPARATE_ADVERSE_SELECTION"
                                                if passive else
                                                "OBSERVED_DEPTH_WITH_SLIPPAGE_EMBEDDED_IN_FILL_PRICE")
            return
        if price is None or bucket not in {"mega", "large", "mid", "small", "micro"}:
            raise ForwardSessionRunnerError("costed fills require a valid price and declared liquidity bucket")
        try:
            schedule = self._resolved_cost_schedule(session_id)
            side = Side.BUY if request["side"] == "BUY" else Side.SELL
            turnover = float(price * filled_quantity)
            # This runner has an observed opposing book and applies price impact through
            # depth walking and tick-rounded adverse execution prices. Do not add a second
            # generic bucket impact estimate to those same fills.
            lines = schedule.one_way(day=config.exchange_date, turnover=turnover, side=side, bucket=bucket,
                                     include_impact=False)
        except (KeyError, TypeError, ValueError) as exc:
            raise ForwardSessionRunnerError(f"invalid configured cost schedule: {exc}") from exc
        schedule_charge = Decimal(str(lines["total"]))
        adverse_charge = Decimal("0.00")
        if passive:
            adverse_charge = Decimal(self._passive_adverse_cost(
                session_id, request, price, filled_quantity))
        receipt["execution_cost_basis"] = ("PASSIVE_TRADE_THROUGH_WITH_SEPARATE_ADVERSE_SELECTION"
                                           if passive else
                                           "OBSERVED_DEPTH_WITH_SLIPPAGE_EMBEDDED_IN_FILL_PRICE")
        receipt["simulated_adverse_selection_cost_inr"] = format(adverse_charge, "f")
        cost_items = [{"order_id": request["order_id"], **lines}]
        if adverse_charge:
            cost_items.append({"order_id": request["order_id"], "type": "SIMULATED_ADVERSE_SELECTION",
                               "total": format(adverse_charge, "f")})
        total_charges = schedule_charge + adverse_charge
        receipt["cost_items"] = cost_items
        receipt["simulated_adverse_selection_cost_inr"] = format(adverse_charge, "f")
        receipt["charges_inr"] = format(total_charges, "f")

    def _passive_adverse_cost(self, session_id: str, request: dict[str, Any],
                              price: Decimal | None, filled_quantity: int) -> str:
        if price is None or request.get("execution_style") != "PASSIVE_LIMIT":
            return "0.00"
        event_ids = request.get("passive_trade_event_ids", [])
        if not event_ids:
            return "0.00"
        rows = self._con.execute(
            "SELECT event_json FROM runner_events WHERE session_id=? AND event_id IN ("
            + ",".join("?" for _ in event_ids) + ") ORDER BY available_at, event_id",
            [session_id, *event_ids]).fetchall()
        limit = Decimal(str(request["limit_price"]))
        prices = [Decimal(str(event["payload"]["price"])) for row in rows
                  if (event := json.loads(row[0]))["kind"] == "tick"]
        through = [value for value in prices if
                   (value < limit if request["side"] == "BUY" else value > limit)]
        if not through:
            through_cost = Decimal(0)
        else:
            move = (limit - min(through)) if request["side"] == "BUY" else (max(through) - limit)
            through_cost = move * filled_quantity
        modeled_cost = price * filled_quantity * Decimal(str(request["adverse_slippage_bps"])) \
            / Decimal(10_000)
        cost = max(through_cost, modeled_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        return format(cost, "f")

    def _resolved_cost_schedule(self, session_id: str):
        from datetime import date

        from financial_brain.costs.india import CostModel, Segment
        from financial_brain.forward_edge.cost_schedule import CostSchedule, CostScheduleBook

        config = self.state_store.config(session_id)
        data = config.constraints.get("cost_schedule")
        if not isinstance(data, dict):
            raise ForwardSessionRunnerError("run does not contain a valid versioned cost schedule")
        try:
            schedule = CostSchedule(
                schedule_id=str(data["schedule_id"]), version=str(data["version"]),
                effective_from=date.fromisoformat(str(data["effective_from"])),
                effective_to=date.fromisoformat(str(data["effective_to"])) if data.get("effective_to") else None,
                segment=Segment(str(data["segment"])), model=CostModel(**data["model"]),
                source_ref=str(data["source_ref"]), source_sha256=str(data["source_sha256"]),
            )
            return CostScheduleBook((schedule,)).resolve(
                schedule_id=schedule.schedule_id, segment=schedule.segment, day=config.exchange_date)
        except (KeyError, TypeError, ValueError) as exc:
            raise ForwardSessionRunnerError(f"invalid configured cost schedule: {exc}") from exc

    def _persist_paper_receipt(self, session_id: str, order_id: str,
                               body: dict[str, Any], *, commit: bool = True) -> None:
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
                               datetime.fromisoformat(body["simulated_at"]),
                               {**body, "provenance": self.provenance})
            if commit:
                self._con.commit()

    def _paper_retry_reconciled_without_effect(self, session_id: str, order_id: str) -> bool:
        """Permit retry only after durable evidence says this exact UNKNOWN simulation did not occur."""
        key = f"paper:{session_id}:{order_id}"
        try:
            state = self._con.execute("SELECT state, reconciled_by FROM outbox WHERE key=?", [key]).fetchone()
            reconciliation = self._con.execute(
                "SELECT took_effect, reconciled_by FROM reconciliations WHERE key=? "
                "ORDER BY reconciliation_id DESC LIMIT 1", [key]).fetchone()
        except sqlite3.Error:
            return False
        return bool(
            state and state[0] == "PENDING" and state[1]
            and reconciliation and reconciliation[0] == 0
            and reconciliation[1] == state[1]
        )

    def reconcile_unknown_paper_intent(self, *, session_id: str, order_id: str,
                                       reconciled_by: str) -> bool:
        """Reconcile a crash-ambiguous local simulation against its durable receipt row."""
        if not reconciled_by.strip():
            raise ForwardSessionRunnerError("paper simulation reconciliation needs an actor")
        outbox = self._outbox_kernel()
        key = f"paper:{session_id}:{order_id}"
        try:
            if key not in outbox.unknown_keys:
                raise ForwardSessionRunnerError("paper intent has no UNKNOWN outbox state")
            receipt = self._con.execute("SELECT receipt_sha256, receipt_json FROM paper_execution_receipts "
                                        "WHERE session_id=? AND order_id=?", [session_id, order_id]).fetchone()
            outbox.resolve_unknown(key, took_effect=receipt is not None, reconciled_by=reconciled_by)
            if receipt is not None:
                body = json.loads(receipt[1])
                self._append_paper_receipt_outcome(
                    session_id=session_id, order_id=order_id, receipt=body, receipt_sha256=receipt[0],
                    occurred_at=capture_local_time(self.clock, label="paper order reconciliation"),
                    reconciled_by=reconciled_by)
            else:
                intent = self._con.execute("SELECT request_json FROM paper_intents "
                                            "WHERE session_id=? AND order_id=?",
                                            [session_id, order_id]).fetchone()
                if intent is None:
                    raise ForwardSessionRunnerError("unknown paper order has no durable intent")
                request = json.loads(intent[0])
                evidence = hashlib.sha256(
                    f"paper-no-effect|{key}|{reconciled_by}".encode("utf-8")).hexdigest()
                self._append_paper_order_event(
                    session_id=session_id, order_id=order_id, state="SUBMITTED",
                    occurred_at=capture_local_time(self.clock, label="paper order reconciliation"),
                    quantity=int(request["quantity"]), filled_quantity=0,
                    reconciliation_evidence_id=evidence,
                    accounting={"source": "MANUAL_NO_EFFECT_RECONCILIATION",
                                "reconciled_by": reconciled_by})
                self._con.commit()
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
            config = self.state_store.config(session_id)
            configured_costs_missing = (config.constraints.get("cost_schedule") is not None and
                                        accounting["filled_order_count"] > 0 and
                                        accounting["status"] != "RECONCILED_NET")
            if accounting["status"] not in {"RECONCILED_GROSS", "RECONCILED_NET"} or \
                    not accounting["cash_reconciled"] or configured_costs_missing:
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
        rows = self._con.execute("SELECT key, state FROM outbox WHERE key LIKE ?",
                                 [f"paper:{session_id}:%"]).fetchall()
        expected = {f"paper:{session_id}:{row[0]}" for row in intents}
        actual = {key for key, state in rows if state == "DISPATCHED"}
        if expected != actual:
            raise ForwardSessionRunnerError("cannot close complete with pending or UNKNOWN paper outbox state")
        prefix = f"{session_id}::"
        unresolved = [history[-1]["state"] for order_ref in self.order_journal.order_ids()
                      if order_ref.startswith(prefix)
                      for history in (self.order_journal.replay(order_ref),)
                      if history and history[-1]["state"] in {
                          "CREATED", "SUBMITTED", "ACCEPTED", "WORKING", "PARTIAL",
                          "CANCEL_PENDING", "TIMED_OUT", "UNKNOWN"}]
        if unresolved:
            raise ForwardSessionRunnerError(
                f"cannot close complete with unresolved paper order states: {sorted(set(unresolved))}")

    def _append_record(self, kind: str, session_id: str, record: dict[str, Any]) -> None:
        event_id = f"{session_id}:{kind}"
        try:
            self.durable.append_lifecycle_record(
                session_id=session_id, kind=kind, event_id=event_id,
                occurred_at=datetime.fromisoformat(record["occurred_at"]), record=record)
        except (DurableJournalError, sqlite3.Error) as exc:
            raise ForwardSessionRunnerError(str(exc)) from exc

    def _append_audit(self, kind: str, session_id: str, record_id: str, occurred_at: datetime,
                      record: dict[str, Any]) -> None:
        try:
            self.durable.append_audit(session_id=session_id, kind=kind, record_id=record_id,
                                      occurred_at=occurred_at, record=record)
        except (DurableJournalError, sqlite3.Error) as exc:
            raise ForwardSessionRunnerError(str(exc)) from exc

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

