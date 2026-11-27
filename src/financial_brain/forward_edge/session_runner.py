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
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from financial_brain.evidence.decision_manifest import ManifestError, canonical_bytes
from financial_brain.forward_edge.execution_scenario import (
    ScenarioFill,
    evaluate_depth_scenario,
    evaluate_passive_scenario,
)
from financial_brain.forward_edge.data_profiles import (
    DataProfileError,
    profiles_from_constraints,
    validate_profile_event,
)
from financial_brain.forward_edge.durable import DurableJournalError, DurableSessionJournal
from financial_brain.forward_edge.heartbeats import SessionHeartbeatLedger
from financial_brain.forward_edge.operating_mode import (
    OperatingMode,
    OperatingModeError,
    OperatingModeLedger,
)
from financial_brain.forward_edge.data_permissions import DataPermission, DataUse
from financial_brain.forward_edge.data_distribution import DistributionThresholds, compare_windows
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


def _timing_telemetry(records: dict[str, Any]) -> dict[str, Any]:
    """Join session clocks by decision while keeping absent stages explicitly unknown."""
    events = {row["event_id"]: row for row in records["events"]}
    risk_by_decision = {row["decision_id"]: row for row in records["risk_verdicts"]}
    intents: dict[str, list[dict[str, Any]]] = {}
    for intent in records["intents"]:
        intents.setdefault(intent["decision_id"], []).append(intent)
    receipts = {row["order_id"]: row for row in records["execution_receipts"]}
    order_events: dict[str, list[dict[str, Any]]] = {}
    for row in records["order_events"]:
        order_events.setdefault(row["paper_order_id"], []).append(row)
    audit_times = {(row["kind"], row["record_id"]): row["occurred_at"] for row in records["audit_log"]}

    signals = []
    for decision in records["decisions"]:
        decision_id = decision["decision_id"]
        input_events = [events[event_id] for event_id in decision["input_event_ids"]
                        if event_id in events]
        source_clocks = [{
            "event_id": event["event_id"], "source": event["source"],
            "event_time": event.get("event_time"), "source_at": event.get("source_at"),
            "exchange_at": event.get("exchange_at"), "received_at": event.get("received_at"),
            "available_at": event.get("available_at"), "ingested_at": event.get("ingested_at"),
            "ingest_clock_source": event.get("ingest_clock_source"),
        } for event in input_events]
        order_rows = intents.get(decision_id, [])
        order_timing = []
        for intent in order_rows:
            order_id = intent["order_id"]
            receipt = receipts.get(order_id)
            history = order_events.get(order_id, [])
            fills = [{"occurred_at": event["occurred_at"], "state": event["state"],
                      "filled_quantity": event["filled_quantity"], "price": event["price"]}
                     for event in history if event["state"] in {"PARTIAL", "FILLED"}]
            order_timing.append({
                "order_id": order_id,
                "intent_at": intent.get("simulated_at"),
                "as_of": intent.get("as_of"),
                "first_executable_after": intent.get("first_executable_after"),
                "expires_at": intent.get("expires_at"),
                "last_good_quote_age_ms": intent.get("last_good_quote_age_ms"),
                "execution_method": intent.get("execution_style"),
                "intent_ledger_recorded_at": audit_times.get(("paper_intent", f"paper_intent:{order_id}")),
                "simulated_arrival_earliest_at": intent.get("earliest_fill_time"),
                "oms_states": [{"state": event["state"], "occurred_at": event["occurred_at"]}
                               for event in history],
                "simulated_arrival_submitted_at": next(
                    (event["occurred_at"] for event in history if event["state"] == "SUBMITTED"), None),
                "execution_status": receipt.get("status") if receipt else "NO_RECEIPT",
                "simulated_fill_at": fills[0]["occurred_at"] if fills else None,
                "execution_ledger_recorded_at": audit_times.get(
                    ("execution_receipt", f"execution_receipt:{order_id}")),
                "fills": fills,
            })
        payload = decision.get("payload") or {}
        # Free-form decision payloads are not custody for a risk-stop or exit event.
        # Keep caller declarations visible as unverified metadata, never as observed timing.
        declared_risk_stop = payload.get("risk_stop_triggered_at")
        declared_position_role = payload.get("position_role")
        risk_record = risk_by_decision.get(decision_id, {})
        missing = []
        if not source_clocks:
            missing.append("SOURCE_EVENT_CLOCKS")
        if any(not event.get("source_at") for event in source_clocks):
            missing.append("SOURCE_TIMESTAMP")
        if any(not event.get("exchange_at") for event in source_clocks):
            missing.append("EXCHANGE_TIMESTAMP")
        if any(not event.get("received_at") for event in source_clocks):
            missing.append("LOCAL_RECEIPT_TIMESTAMP")
        if not decision.get("feature_ready_at"):
            missing.append("FEATURE_READY_TIMESTAMP")
        if not decision.get("feature_started_at"):
            missing.append("FEATURE_COMPUTATION_START_TIMESTAMP")
        if not decision.get("model_inference_started_at") or not decision.get("model_inference_completed_at"):
            missing.append("MODEL_INFERENCE_TIMESTAMPS")
        if decision["outcome"] == "TRADE" and not risk_record:
            missing.append("OMS_RISK_ASSESSMENT_TIMESTAMP")
        if decision["outcome"] == "TRADE" and not order_timing:
            missing.append("SIMULATED_ORDER_OR_FILL")
        if decision["outcome"] == "TRADE" and not any(row["fills"] for row in order_timing):
            missing.append("SIMULATED_FILL_TIMESTAMP")
        if decision["outcome"] == "TRADE" and not any(row["simulated_arrival_submitted_at"] for row in order_timing):
            missing.append("SIMULATED_ORDER_ARRIVAL_TIMESTAMP")
        if decision["outcome"] == "TRADE" and not any(row["execution_ledger_recorded_at"] for row in order_timing):
            missing.append("EXECUTION_LEDGER_RECORD_TIMESTAMP")
        if decision["outcome"] == "TRADE":
            missing.append("RISK_STOP_TRIGGER_TIMESTAMP")
        if decision["outcome"] == "TRADE":
            missing.append("EXIT_ROLE_OR_EXECUTION_TIMESTAMP")
        signals.append({
            "decision_id": decision_id, "strategy_id": decision["strategy_id"],
            "outcome": decision["outcome"], "source_clocks": source_clocks,
            "as_of": decision.get("as_of"),
            "first_executable_after": min((row["first_executable_after"] for row in order_timing
                                            if row["first_executable_after"]), default=None),
            "expires_at": min((row["expires_at"] for row in order_timing if row["expires_at"]), default=None),
            "last_good_quote_age_ms": decision.get("last_good_quote_age_ms"),
            "execution_method": sorted({row["execution_method"] for row in order_timing
                                        if row["execution_method"]}),
            "feature_computation_started_at": decision.get("feature_started_at"),
            "feature_ready_at": decision.get("feature_ready_at"),
            "model_inference_started_at": decision.get("model_inference_started_at"),
            "model_inference_completed_at": decision.get("model_inference_completed_at"),
            "signal_decision_at": decision["decided_at"],
            "decision_ledger_recorded_at": audit_times.get(("decision", f"decision:{decision_id}")),
            "oms_risk_assessed_at": risk_record.get("assessed_at"),
            "risk_ledger_recorded_at": audit_times.get(
                ("risk_verdict", f"risk_verdict:{risk_record['verdict_id']}")) if risk_record else None,
            "oms_risk_allowed": risk_record.get("allowed"),
            "orders": order_timing, "risk_stop_triggered_at": None,
            "caller_declared_risk_stop_at_unverified": declared_risk_stop,
            "caller_declared_position_role_unverified": declared_position_role,
            "exit_execution": [],
            "missing_stages": sorted(set(missing)),
            "status": "COMPLETE" if not missing else "INCOMPLETE",
        })
    return {"schema": "fb.forward_session_timing_telemetry.v1", "signals": signals,
            "signal_count": len(signals), "complete_signal_count": sum(
                signal["status"] == "COMPLETE" for signal in signals),
            "claim_scope": "PAPER_SESSION_EVIDENCE"}


class ForwardSessionRunnerError(ValueError):
    """Session runner rejected an event, decision or lifecycle operation."""


def _validate_completed_bar(event: MarketEvent, session_open_at: datetime,
                            session_close_at: datetime) -> None:
    """Require a complete bar to fit an official session-relative interval."""
    payload = event.payload
    try:
        if (session_open_at.tzinfo is None or session_open_at.utcoffset() is None or
                session_close_at.tzinfo is None or session_close_at.utcoffset() is None):
            raise ValueError("sourced session bounds must be timezone-aware")
        interval = payload.get("interval_seconds")
        if type(interval) is not int or interval not in {60, 900}:
            raise ValueError("bar interval must be 60 or 900 seconds")
        raw_start, raw_end = payload.get("bar_start_at"), payload.get("bar_end_at")
        if not isinstance(raw_start, str) or not isinstance(raw_end, str):
            raise ValueError("bar start and end timestamps are required")
        start, end = datetime.fromisoformat(raw_start), datetime.fromisoformat(raw_end)
        if (start.tzinfo is None or start.utcoffset() is None or
                end.tzinfo is None or end.utcoffset() is None):
            raise ValueError("bar start and end timestamps must include timezone offsets")
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        opens = session_open_at.astimezone(timezone.utc)
        closes = session_close_at.astimezone(timezone.utc)
        interval_delta = timedelta(seconds=interval)
        if end - start != interval_delta or event.event_time != end:
            raise ValueError("bar timestamps do not match the declared complete interval")
        if start < opens or end > closes:
            raise ValueError("bar interval is outside the official exchange session")
        elapsed = start - opens
        if elapsed % interval_delta != timedelta(0):
            raise ValueError("bar interval is not aligned to the official exchange session open")
        if event.received_at < end or event.available_at < end:
            raise ValueError("completed bar arrived before its interval ended")
    except (TypeError, ValueError) as exc:
        raise ForwardSessionRunnerError(f"completed bar rejected: {exc}") from exc


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
        self.heartbeats = SessionHeartbeatLedger(self._con)
        self.operating_modes = OperatingModeLedger(self._con)
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_session_reports (
            session_id TEXT PRIMARY KEY, status TEXT NOT NULL, report_sha256 TEXT NOT NULL,
            report_json TEXT NOT NULL)""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_checkpoints (
            session_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, phase TEXT NOT NULL,
            checkpoint_sha256 TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
            PRIMARY KEY(session_id, checkpoint_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_event_cursors (
            session_id TEXT NOT NULL, consumer_id TEXT NOT NULL, event_offset INTEGER NOT NULL,
            event_id TEXT NOT NULL, updated_at TEXT NOT NULL, cursor_sha256 TEXT NOT NULL,
            PRIMARY KEY(session_id, consumer_id))""")
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

    def record_component_heartbeat(self, *, session_id: str, component: str, heartbeat_id: str,
                                  occurred_at: datetime | None = None,
                                  last_good_event_id: str | None = None,
                                  last_good_event_at: datetime | None = None) -> bool:
        """Accept an explicit component heartbeat; missing producers remain degraded in records()."""
        try:
            self.state_store.state(session_id)
        except Exception as exc:
            raise ForwardSessionRunnerError("heartbeat requires a known forward session") from exc
        now = occurred_at or capture_local_time(self.clock, label="component heartbeat time")
        try:
            return self.heartbeats.record(
                session_id=session_id, component=component, heartbeat_id=heartbeat_id,
                occurred_at=now, last_good_event_id=last_good_event_id,
                last_good_event_at=last_good_event_at)
        except ValueError as exc:
            raise ForwardSessionRunnerError(f"component heartbeat rejected: {exc}") from exc
        self.state_store.close()

    def set_operating_mode(self, *, session_id: str, mode: str | OperatingMode,
                           event_id: str, reason: str, occurred_at: datetime | None = None) -> bool:
        """Persist an explicit pause, degradation, recovery or halt decision."""
        try:
            self.state_store.state(session_id)
            return self.operating_modes.transition(
                session_id, mode, event_id=event_id,
                occurred_at=occurred_at or capture_local_time(self.clock, label="operating mode transition"),
                reason=reason)
        except (ValueError, OperatingModeError) as exc:
            raise ForwardSessionRunnerError(f"operating mode transition rejected: {exc}") from exc

    def begin(self, session_id: str, config: ForwardRunConfig, *, now: datetime | None = None,
              preflight: dict[str, bool]) -> SessionState:
        """Create or resume a session, recording every preflight check before opening it."""
        try:
            profiles_from_constraints(config.constraints, config.instrument_universe)
        except DataProfileError as exc:
            raise ForwardSessionRunnerError(f"invalid frozen instrument data profiles: {exc}") from exc
        now = now or capture_local_time(self.clock, label="session start")
        self.state_store.create(session_id, config, now=now)
        try:
            self.operating_modes.initialize(session_id, occurred_at=now)
        except OperatingModeError as exc:
            raise ForwardSessionRunnerError(f"operating mode initialization failed: {exc}") from exc
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

    def run_event_loop(self, *, session_id: str, config: ForwardRunConfig,
                       events: Iterable[MarketEvent], session_open_at: datetime,
                       preflight: dict[str, bool], consumer_id: str,
                       on_market_event: Callable[[MarketEvent, "ForwardSessionRunner"], None],
                       evaluate_protective_exits: Callable[[MarketEvent, "ForwardSessionRunner"], None],
                       on_completed_bar: Callable[[MarketEvent, "ForwardSessionRunner"], None] | None = None,
                       venue_calendar: VenueCalendar | None = None, venue: str | None = None,
                       on_close: Callable[["ForwardSessionRunner"], None] | None = None,
                       session_close_at: datetime | None = None,
                       close_complete: bool = False) -> dict[str, Any]:
        """Run one restartable, deterministic session event loop.

        Events are made available one at a time in canonical time/sequence/ID order. The
        market handler runs after durable ingestion; the optional completed-bar handler
        runs only for BAR events explicitly tagged ``complete=True``; the protective-
        exit evaluator then runs for every event (including quotes between strategy bars).
        A durable consumer
        cursor advances only after both handlers return. Handlers must therefore use
        idempotent decision/order IDs: a crash after a handler side effect but before its
        cursor acknowledgement intentionally replays that event.

        Passing ``close_complete=True`` requests closeout after all pending events have
        been handled. Otherwise the session stays open for a later call or supervised
        closeout. A failed handler leaves the session open and the event unacknowledged,
        so a restart can retry it without losing the event.
        """
        if not session_id.strip() or not consumer_id.strip():
            raise ForwardSessionRunnerError("session and event consumer IDs are required")
        if session_open_at.tzinfo is None or session_open_at.utcoffset() is None:
            raise ForwardSessionRunnerError("session open clock must be timezone aware")
        if not callable(on_market_event) or not callable(evaluate_protective_exits):
            raise ForwardSessionRunnerError("market and protective-exit handlers are required")
        try:
            profiles = profiles_from_constraints(config.constraints, config.instrument_universe)
        except DataProfileError as exc:
            raise ForwardSessionRunnerError(f"invalid frozen instrument data profiles: {exc}") from exc
        if profiles is None:
            raise ForwardSessionRunnerError(
                "deterministic event loop requires an explicit data profile for every frozen instrument")
        if on_close is not None and not callable(on_close):
            raise ForwardSessionRunnerError("session close handler must be callable")
        if on_completed_bar is not None and not callable(on_completed_bar):
            raise ForwardSessionRunnerError("completed-bar handler must be callable")
        if on_completed_bar is not None and venue_calendar is None:
            raise ForwardSessionRunnerError("completed-bar forecasts require a sourced venue calendar")
        if venue_calendar is not None:
            if venue is None or not venue.strip():
                raise ForwardSessionRunnerError("venue is required with a sourced session calendar")
        if type(close_complete) is not bool:
            raise ForwardSessionRunnerError("close_complete must be boolean")
        if close_complete and (session_close_at is None or session_close_at.tzinfo is None or
                               session_close_at.utcoffset() is None):
            raise ForwardSessionRunnerError("complete closeout requires an aware session close clock")

        event_batch = list(events)
        if any(not isinstance(event, MarketEvent) for event in event_batch):
            raise ForwardSessionRunnerError("event loop accepts normalized MarketEvent values only")
        if any(event.kind is EventKind.BAR and "complete" in event.payload and
               type(event.payload["complete"]) is not bool for event in event_batch):
            raise ForwardSessionRunnerError("bar complete marker must be boolean")
        ordered = sorted(event_batch, key=lambda event: (
            event.available_at, event.event_time,
            -1 if event.sequence is None else event.sequence, event.event_id))
        if venue_calendar is not None:
            if venue is None:
                raise ForwardSessionRunnerError("venue is required with a sourced session calendar")
            state = self.plan_calendar_day(
                session_id, config, venue=venue, calendar=venue_calendar,
                now=session_open_at, preflight=preflight)
        else:
            self.state_store.create(session_id, config, now=session_open_at)
            state = self.state_store.state(session_id)
            if state is SessionState.PLANNED:
                state = self.begin(session_id, config, now=session_open_at, preflight=preflight)
        if state is not SessionState.OPEN:
            return {"session_id": session_id, "status": state.value, "processed_event_ids": [],
                    "persisted_event_ids": [], "close_report": None}

        persisted_event_ids: list[str] = []
        processed_event_ids: list[str] = []

        def drain_consumer() -> None:
            while pending := self.read_events_after(session_id, consumer_id, limit=1):
                row = pending[0]
                event = MarketEvent.from_dict(row["event"])
                if (event.kind is EventKind.BAR and event.payload.get("complete") is True and
                        on_completed_bar is not None):
                    if venue_calendar is None:
                        raise ForwardSessionRunnerError("completed-bar forecasts require a sourced venue calendar")
                    opens, closes = venue_calendar.session_bounds_utc(event.venue, config.exchange_date)
                    _validate_completed_bar(event, opens, closes)
                on_market_event(event, self)
                # Forecast cadence is bar-driven and separate from the position monitor.
                # Missing or false completion evidence fails closed for forecasting.
                if (event.kind is EventKind.BAR and event.payload.get("complete") is True and
                        on_completed_bar is not None):
                    on_completed_bar(event, self)
                # This is deliberately independent of forecast cadence: every source
                # event gives the position monitor a chance to evaluate protective exits.
                evaluate_protective_exits(event, self)
                cursor = self.event_cursor(session_id, consumer_id)
                self.acknowledge_event_offset(
                    session_id, consumer_id, expected_offset=(cursor or {}).get("event_offset", 0),
                    through_offset=row["offset"], event_ids=(row["event_id"],),
                    now=datetime.fromisoformat(row["ingested_at"]))
                processed_event_ids.append(row["event_id"])

        # Retry the durable unacknowledged prefix before admitting newer events. This
        # prevents callbacks from seeing later events in the session ledger.
        drain_consumer()
        for event in ordered:
            if (event.kind is EventKind.BAR and event.payload.get("complete") is True and
                    on_completed_bar is not None):
                if venue_calendar is None:
                    raise ForwardSessionRunnerError("completed-bar forecasts require a sourced venue calendar")
                opens, closes = venue_calendar.session_bounds_utc(event.venue, config.exchange_date)
                _validate_completed_bar(event, opens, closes)
            result = self.ingest(session_id, config, [event])
            if result["persisted"]:
                persisted_event_ids.append(event.event_id)
            drain_consumer()

        close_report = None
        if close_complete:
            if on_close is not None:
                on_close(self)
            close_report = self.close_session(session_id, complete=True, now=session_close_at)
        return {"session_id": session_id,
                "status": close_report["status"] if close_report else SessionState.OPEN.value,
                "persisted_event_ids": persisted_event_ids,
                "processed_event_ids": processed_event_ids,
                "close_report": close_report}

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
        try:
            self.operating_modes.initialize(session_id, occurred_at=now)
        except OperatingModeError as exc:
            raise ForwardSessionRunnerError(f"operating mode initialization failed: {exc}") from exc
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
        try:
            profiles = profiles_from_constraints(config.constraints, config.instrument_universe)
        except DataProfileError as exc:
            raise ForwardSessionRunnerError(f"invalid frozen instrument data profiles: {exc}") from exc
        persisted_events: list[tuple[MarketEvent, datetime]] = []
        self._con.execute("SAVEPOINT ingest_batch")
        try:
            for event in batch:
                counts["received"] += 1
                if event.instrument_id not in expected:
                    raise ForwardSessionRunnerError("event instrument is outside frozen universe")
                if profiles is not None:
                    try:
                        validate_profile_event(profiles[event.instrument_id], event)
                    except DataProfileError as exc:
                        raise ForwardSessionRunnerError(f"instrument data profile rejected event: {exc}") from exc
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
                persisted_events.append((event, ingested_at))
        except Exception as exc:
            self._con.execute("ROLLBACK TO ingest_batch")
            self._con.execute("RELEASE ingest_batch")
            self._con.rollback()
            self._record_data_rejection(session_id, "ingest_batch", str(exc), batch)
            raise
        self._con.execute("RELEASE ingest_batch")
        self._con.commit()
        batch_ingested_at = max((ingested_at for _, ingested_at in persisted_events), default=None)
        usable = [(event, ingested_at) for event, ingested_at in persisted_events
                  if event.kind in {EventKind.QUOTE, EventKind.TICK}]
        if usable:
            if batch_ingested_at is None:
                raise ForwardSessionRunnerError("persisted quote lacks its ingest clock")
            latest, _ = max(usable, key=lambda row: (row[0].available_at, row[0].event_id))
            prior_heartbeat = self.heartbeats.last_heartbeat_at(session_id, "quote_feed")
            quote_heartbeat_at = max(batch_ingested_at, prior_heartbeat) if prior_heartbeat else batch_ingested_at
            heartbeat_suffix = quote_heartbeat_at.isoformat()
            self.record_component_heartbeat(
                session_id=session_id, component="quote_feed",
                heartbeat_id=f"quote:{latest.event_id}:{heartbeat_suffix}", occurred_at=quote_heartbeat_at,
                last_good_event_id=latest.event_id, last_good_event_at=latest.available_at)
            self.record_component_heartbeat(
                session_id=session_id, component="ledger_writer",
                heartbeat_id=f"ledger:{latest.event_id}:{heartbeat_suffix}", occurred_at=quote_heartbeat_at,
                last_good_event_id=latest.event_id, last_good_event_at=latest.available_at)
        bars = [(event, ingested_at) for event, ingested_at in persisted_events
                if event.kind is EventKind.BAR]
        if bars:
            if batch_ingested_at is None:
                raise ForwardSessionRunnerError("persisted bar lacks its ingest clock")
            latest_bar, _ = max(bars, key=lambda row: (row[0].available_at, row[0].event_id))
            prior_heartbeat = self.heartbeats.last_heartbeat_at(session_id, "completed_bars")
            bar_heartbeat_at = max(batch_ingested_at, prior_heartbeat) if prior_heartbeat else batch_ingested_at
            self.record_component_heartbeat(
                session_id=session_id, component="completed_bars",
                heartbeat_id=f"bar:{latest_bar.event_id}:{bar_heartbeat_at.isoformat()}",
                occurred_at=bar_heartbeat_at,
                last_good_event_id=latest_bar.event_id, last_good_event_at=latest_bar.available_at)
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

    def compare_archive_windows(self, archive: QuoteArchive, *, venue: str, instrument_id: str,
                                reference_start: datetime, reference_end: datetime,
                                current_start: datetime, current_end: datetime, as_of: datetime,
                                expected_reference_quotes: int, expected_current_quotes: int,
                                max_transport_lag: timedelta,
                                thresholds: DistributionThresholds = DistributionThresholds()) -> dict[str, Any]:
        """Produce an evidence-ready price-regime/feed-distribution comparison for a paper session.

        This diagnostic is intentionally separate from order generation: it reports data
        health and price regime independently and cannot create a trade or pass a risk gate.
        """
        if current_end - current_start != reference_end - reference_start:
            raise ForwardSessionRunnerError("reference and current windows must have equal duration")
        try:
            reference_price = archive.price_window(start=reference_start, end=reference_end, as_of=as_of,
                                                   venue=venue, instrument_id=instrument_id)
            current_price = archive.price_window(start=current_start, end=current_end, as_of=as_of,
                                                 venue=venue, instrument_id=instrument_id)
            reference_provider = archive.provider_window(
                start=reference_start, end=reference_end, as_of=as_of,
                expected_quote_observations=expected_reference_quotes,
                max_transport_lag=max_transport_lag)
            current_provider = archive.provider_window(
                start=current_start, end=current_end, as_of=as_of,
                expected_quote_observations=expected_current_quotes,
                max_transport_lag=max_transport_lag)
            comparison = compare_windows(reference_price, current_price, reference_provider,
                                         current_provider, thresholds=thresholds)
        except (ValueError, TypeError) as exc:
            raise ForwardSessionRunnerError(f"archive window comparison failed: {exc}") from exc
        return {
            "schema": "fb.price_provider_distribution_comparison.v1",
            "venue": venue.upper(), "instrument_id": instrument_id,
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "reference": {"start": reference_start.astimezone(timezone.utc).isoformat(),
                          "end": reference_end.astimezone(timezone.utc).isoformat(),
                          "price_observations": reference_price.observations,
                          "realized_volatility": reference_price.realized_volatility,
                          "net_return": reference_price.net_return,
                          "provider_observations": reference_provider.observations,
                          "quote_coverage": reference_provider.quote_coverage,
                          "stale_fraction": reference_provider.stale_fraction,
                          "out_of_order_fraction": reference_provider.out_of_order_fraction,
                          "missing_payload_fraction": reference_provider.missing_payload_fraction,
                          "source_mix": dict(reference_provider.source_mix),
                          "schema_sha256": reference_provider.schema_sha256},
            "current": {"start": current_start.astimezone(timezone.utc).isoformat(),
                        "end": current_end.astimezone(timezone.utc).isoformat(),
                        "price_observations": current_price.observations,
                        "realized_volatility": current_price.realized_volatility,
                        "net_return": current_price.net_return,
                        "provider_observations": current_provider.observations,
                        "quote_coverage": current_provider.quote_coverage,
                        "stale_fraction": current_provider.stale_fraction,
                        "out_of_order_fraction": current_provider.out_of_order_fraction,
                        "missing_payload_fraction": current_provider.missing_payload_fraction,
                        "source_mix": dict(current_provider.source_mix),
                        "schema_sha256": current_provider.schema_sha256},
            "comparison": {"price_regime_shift": comparison.price_regime_shift,
                           "provider_distribution_shift": comparison.provider_distribution_shift,
                           "price_reasons": list(comparison.price_reasons),
                           "provider_reasons": list(comparison.provider_reasons),
                           "source_mix_total_variation": comparison.source_mix_total_variation,
                           "status": comparison.status},
            "trading_action": "NONE_DIAGNOSTIC_ONLY",
        }

    def decide(self, *, session_id: str, strategy_id: str, decision_id: str,
               outcome: str, input_event_ids: tuple[str, ...], decided_at: datetime | None = None,
               reason_codes: tuple[str, ...] = (), payload: dict[str, Any] | None = None,
               feature_ready_at: datetime | None = None, feature_started_at: datetime | None = None,
               model_inference_started_at: datetime | None = None,
               model_inference_completed_at: datetime | None = None) -> bool:
        """Record a decision only when every referenced source event was already available."""
        if self.state_store.state(session_id) is not SessionState.OPEN:
            raise ForwardSessionRunnerError("decisions are accepted only in an OPEN session")
        if not all(value.strip() for value in (strategy_id, decision_id)):
            raise ForwardSessionRunnerError("strategy and decision IDs are required")
        if outcome not in {"TRADE", "NO_TRADE", "WITHHELD", "NO_SIGNAL", "DATA_UNUSABLE"}:
            raise ForwardSessionRunnerError("unsupported forward decision outcome")
        decided_at = decided_at or capture_local_time(self.clock, label="decision timestamp")
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ForwardSessionRunnerError("decision time must be timezone aware")
        if outcome != "TRADE" and not reason_codes:
            raise ForwardSessionRunnerError("non-trade decision requires a reason code")
        if not input_event_ids and outcome != "DATA_UNUSABLE":
            raise ForwardSessionRunnerError("decision must reference at least one source event")
        unique_ids = tuple(sorted(set(input_event_ids)))
        if len(unique_ids) != len(input_event_ids):
            raise ForwardSessionRunnerError("decision input event IDs must be unique")
        rows = self._con.execute(
            f"SELECT event_id, kind, available_at, event_json FROM runner_events WHERE session_id=? "
            f"AND kind NOT IN ('preflight','calendar_plan') AND event_id IN ({','.join('?' for _ in unique_ids)})",
            [session_id, *unique_ids]).fetchall() if unique_ids else []
        if len(rows) != len(unique_ids):
            raise ForwardSessionRunnerError("decision references an event absent from this session")
        clock = decided_at.astimezone(timezone.utc)
        if any(datetime.fromisoformat(available) > clock for _, _, available, _ in rows):
            raise ForwardSessionRunnerError("decision uses a source event unavailable at decision time")
        feature_clock = None
        optional_clocks = {
            "feature_started_at": feature_started_at,
            "feature_ready_at": feature_ready_at,
            "model_inference_started_at": model_inference_started_at,
            "model_inference_completed_at": model_inference_completed_at,
        }
        normalized_clocks: dict[str, datetime | None] = {}
        for name, value in optional_clocks.items():
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ForwardSessionRunnerError(f"{name.replace('_', ' ')} must be timezone-aware")
            normalized_clocks[name] = value.astimezone(timezone.utc) if value is not None else None
        if not rows and any(value is not None for value in normalized_clocks.values()):
            raise ForwardSessionRunnerError("feature and inference clocks cannot be attested without source inputs")
        if feature_ready_at is not None:
            if not rows:
                raise ForwardSessionRunnerError("feature-ready time cannot be attested without source inputs")
            feature_clock = normalized_clocks["feature_ready_at"]
            latest_input = max(datetime.fromisoformat(available).astimezone(timezone.utc)
                               for _, _, available, _ in rows)
            assert feature_clock is not None
            if feature_clock < latest_input or feature_clock > clock:
                raise ForwardSessionRunnerError(
                    "feature-ready time must follow all input availability and precede the signal decision")
        feature_start_clock = normalized_clocks["feature_started_at"]
        inference_start_clock = normalized_clocks["model_inference_started_at"]
        inference_end_clock = normalized_clocks["model_inference_completed_at"]
        if feature_start_clock is not None:
            if not rows:
                raise ForwardSessionRunnerError("feature computation cannot be attested without source inputs")
            earliest_input = min(datetime.fromisoformat(available).astimezone(timezone.utc)
                                 for _, _, available, _ in rows)
            if feature_start_clock < earliest_input or feature_start_clock > clock:
                raise ForwardSessionRunnerError("feature computation start must follow inputs and precede decision")
        if feature_start_clock and feature_clock and feature_start_clock > feature_clock:
            raise ForwardSessionRunnerError("feature computation start cannot follow feature-ready time")
        if inference_start_clock and feature_clock and inference_start_clock < feature_clock:
            raise ForwardSessionRunnerError("model inference cannot start before features are ready")
        if inference_start_clock and inference_end_clock and inference_end_clock < inference_start_clock:
            raise ForwardSessionRunnerError("model inference completion cannot precede its start")
        if inference_end_clock and inference_end_clock > clock:
            raise ForwardSessionRunnerError("model inference cannot complete after the signal decision")
        max_quote_age = self._max_quote_age(session_id)
        stale = []
        quote_ages_ms = []
        input_available = []
        for event_id, kind, _, event_json in rows:
            input_event = MarketEvent.from_dict(json.loads(event_json))
            input_available.append(input_event.available_at.astimezone(timezone.utc))
            if kind != "quote":
                continue
            source_time = input_event.event_time.astimezone(timezone.utc)
            quote_ages_ms.append((clock - source_time).total_seconds() * 1000)
            if source_time > clock or clock - source_time > timedelta(milliseconds=max_quote_age):
                stale.append(event_id)
        if stale and outcome != "DATA_UNUSABLE":
            current_mode = self.operating_modes.mode(session_id)
            if current_mode not in {OperatingMode.DEGRADED, OperatingMode.HALTED}:
                self.set_operating_mode(
                    session_id=session_id, mode=OperatingMode.DEGRADED,
                    event_id=f"stale-input:{decision_id}", occurred_at=clock,
                    reason="stale decision quote: " + ",".join(sorted(stale)))
            raise ForwardSessionRunnerError(
                f"decision quote exceeds configured maximum age of {max_quote_age}ms: {', '.join(sorted(stale))}")
        body = {"session_id": session_id, "strategy_id": strategy_id, "decision_id": decision_id,
                "outcome": outcome, "decided_at": clock.isoformat(), "input_event_ids": list(unique_ids),
                "as_of": max(input_available).isoformat() if input_available else None,
                "last_good_quote_age_ms": max(quote_ages_ms) if quote_ages_ms else None,
                "feature_ready_at": feature_clock.isoformat() if feature_clock else None,
                "feature_started_at": feature_start_clock.isoformat() if feature_start_clock else None,
                "model_inference_started_at": inference_start_clock.isoformat() if inference_start_clock else None,
                "model_inference_completed_at": inference_end_clock.isoformat() if inference_end_clock else None,
                "reason_codes": sorted(set(reason_codes)), "payload": payload or {}}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self._con.execute("SELECT record_sha256, record_json FROM runner_decisions WHERE session_id=? AND decision_id=?",
                                     [session_id, decision_id]).fetchone()
        if existing:
            if existing[0] != digest:
                prior_body = json.loads(existing[1])
                legacy_keys = {"feature_ready_at", "feature_started_at", "model_inference_started_at",
                               "model_inference_completed_at"}
                legacy_body = {key: value for key, value in body.items() if key not in legacy_keys}
                if not any(key in prior_body for key in legacy_keys) and prior_body == legacy_body:
                    return False
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
        self.record_component_heartbeat(
            session_id=session_id, component="strategy_process",
            heartbeat_id=f"decision:{decision_id}", occurred_at=clock,
            last_good_event_id=decision_id, last_good_event_at=clock)
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
        result = {"events": events, "decisions": decisions, "lifecycle": lifecycle,
                  "intents": intents, "execution_receipts": receipts, "session_reports": reports,
                  "order_events": order_events,
                  "risk_verdicts": risk_verdicts, "decision_manifests": decision_manifests,
                  "data_rejections": data_rejections,
                  "audit_log": audit_log, "accounting": accounting}
        result["timing_telemetry"] = _timing_telemetry(result)
        result["component_heartbeats"] = self.heartbeats.latest(session_id)
        result["operating_mode"] = self.operating_modes.mode(session_id).value
        result["operating_mode_events"] = self.operating_modes.events(session_id)
        return result

    def read_events_after(self, session_id: str, consumer_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Read persisted market events after a consumer's acknowledged insertion offset."""
        if not consumer_id.strip():
            raise ForwardSessionRunnerError("event cursor consumer ID is required")
        if limit < 1 or limit > 10000:
            raise ForwardSessionRunnerError("event cursor read limit must be between 1 and 10000")
        row = self._con.execute(
            "SELECT event_offset FROM runner_event_cursors WHERE session_id=? AND consumer_id=?",
            [session_id, consumer_id]).fetchone()
        offset = int(row[0]) if row else 0
        rows = self._con.execute(
            "SELECT rowid,event_id,kind,available_at,ingested_at,ingest_clock_source,event_sha256,event_json "
            "FROM runner_events WHERE session_id=? AND rowid>? "
            "AND kind NOT IN ('preflight','calendar_plan') ORDER BY rowid LIMIT ?",
            [session_id, offset, limit]).fetchall()
        return [{"offset": int(item[0]), "event_id": item[1], "kind": item[2],
                 "available_at": item[3], "ingested_at": item[4], "ingest_clock_source": item[5],
                 "event_sha256": item[6], "event": json.loads(item[7])} for item in rows]

    def acknowledge_event_offset(self, session_id: str, consumer_id: str, *,
                                 expected_offset: int, through_offset: int,
                                 event_ids: tuple[str, ...], now: datetime | None = None) -> dict[str, Any]:
        """Advance an event cursor over an exact durable prefix using compare-and-swap."""
        consumer_id = consumer_id.strip()
        if not consumer_id:
            raise ForwardSessionRunnerError("event cursor consumer ID is required")
        if expected_offset < 0 or through_offset < 1 or through_offset <= expected_offset:
            raise ForwardSessionRunnerError("event cursor offsets must advance from a non-negative offset")
        if not event_ids or len(event_ids) != len(set(event_ids)):
            raise ForwardSessionRunnerError("event cursor acknowledgement requires unique event IDs")
        now = now or capture_local_time(self.clock, label="event cursor acknowledgement")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ForwardSessionRunnerError("event cursor timestamp must be timezone aware")
        updated_at = now.astimezone(timezone.utc).isoformat()
        self._con.execute("BEGIN IMMEDIATE")
        try:
            current = self._con.execute(
                "SELECT event_offset,event_id,updated_at,cursor_sha256 FROM runner_event_cursors "
                "WHERE session_id=? AND consumer_id=?", [session_id, consumer_id]).fetchone()
            current_offset = int(current[0]) if current else 0
            if current and current_offset == through_offset:
                row = self._con.execute(
                    "SELECT event_id FROM runner_events WHERE session_id=? AND rowid=?",
                    [session_id, through_offset]).fetchone()
                if row and row[0] == event_ids[-1]:
                    self._con.commit()
                    return {"session_id": session_id, "consumer_id": consumer_id,
                            "event_offset": current_offset, "event_id": current[1],
                            "updated_at": current[2], "cursor_sha256": current[3], "idempotent": True}
            if current_offset != expected_offset:
                raise ForwardSessionRunnerError("event cursor changed; reread before acknowledging")
            rows = self._con.execute(
                "SELECT rowid,event_id FROM runner_events WHERE session_id=? AND rowid>? AND rowid<=? "
                "AND kind NOT IN ('preflight','calendar_plan') ORDER BY rowid",
                [session_id, expected_offset, through_offset]).fetchall()
            if (not rows or int(rows[-1][0]) != through_offset or
                    tuple(str(row[1]) for row in rows) != event_ids):
                raise ForwardSessionRunnerError("event cursor acknowledgement does not match the exact persisted prefix")
            body = {"schema": "fb.forward-event-cursor.v1", "session_id": session_id,
                    "consumer_id": consumer_id, "event_offset": through_offset,
                    "event_id": event_ids[-1], "event_ids_sha256": self._sha256(list(event_ids)),
                    "updated_at": updated_at}
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            self._con.execute(
                "INSERT INTO runner_event_cursors VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(session_id,consumer_id) DO UPDATE SET event_offset=excluded.event_offset, "
                "event_id=excluded.event_id,updated_at=excluded.updated_at,cursor_sha256=excluded.cursor_sha256",
                [session_id, consumer_id, through_offset, event_ids[-1], updated_at, digest])
            self._con.commit()
            return body | {"cursor_sha256": digest, "idempotent": False}
        except Exception:
            self._con.rollback()
            raise

    def event_cursor(self, session_id: str, consumer_id: str) -> dict[str, Any] | None:
        """Return the latest durable consumer offset, or None before the first acknowledgement."""
        row = self._con.execute(
            "SELECT event_offset,event_id,updated_at,cursor_sha256 FROM runner_event_cursors "
            "WHERE session_id=? AND consumer_id=?", [session_id, consumer_id]).fetchone()
        if row is None:
            return None
        return {"session_id": session_id, "consumer_id": consumer_id, "event_offset": int(row[0]),
                "event_id": row[1], "updated_at": row[2], "cursor_sha256": row[3]}

    def heartbeat_health(self, session_id: str, *, now: datetime | None = None,
                         stale_after: timedelta = timedelta(seconds=60)) -> dict[str, Any]:
        """Return a wall-clock health view separately from deterministic session records."""
        observed = now or capture_local_time(self.clock, label="heartbeat health cutoff")
        return self.heartbeats.snapshot(session_id, now=observed, stale_after=stale_after)

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
            hashes["event_cursors"] = self._event_cursors_hash(session_id)
            if existing:
                prior = json.loads(existing[1])
                prior_hashes = prior.get("record_hashes", {})
                legacy_hashes = {key: value for key, value in hashes.items() if key != "event_cursors"}
                if (prior.get("session_state") != state.value or
                        prior_hashes not in (hashes, legacy_hashes)):
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
            if "event_cursors" not in body.get("record_hashes", {}):
                current.pop("event_cursors", None)
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
        hashes["event_cursors"] = self._event_cursors_hash(session_id)
        return hashes

    def _event_cursors_hash(self, session_id: str) -> str:
        cursors = [{"consumer_id": row[0], "event_offset": int(row[1]), "event_id": row[2],
                    "updated_at": row[3], "cursor_sha256": row[4]}
                   for row in self._con.execute(
                       "SELECT consumer_id,event_offset,event_id,updated_at,cursor_sha256 "
                       "FROM runner_event_cursors WHERE session_id=? ORDER BY consumer_id",
                       [session_id]).fetchall()]
        return self._sha256(cursors)

    @staticmethod
    def _sha256(value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _instrument_halted(self, session_id: str, instrument_id: str, at: datetime) -> bool:
        """Fail closed when the latest available halt state is HALTED."""
        cutoff = at.astimezone(timezone.utc).isoformat()
        rows = self._con.execute(
            "SELECT event_json FROM runner_events WHERE session_id=? AND kind=? AND available_at<=? "
            "ORDER BY available_at, event_id", [session_id, EventKind.HALT.value, cutoff]).fetchall()
        relevant = [MarketEvent.from_dict(json.loads(row[0])) for row in rows]
        relevant = [event for event in relevant if event.instrument_id == instrument_id]
        if not relevant:
            return False
        latest_at = max(event.available_at for event in relevant)
        latest_states = {str(event.payload["status"]) for event in relevant if event.available_at == latest_at}
        return "HALTED" in latest_states

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
        try:
            profiles = profiles_from_constraints(config.constraints, config.instrument_universe)
            if profiles is not None:
                validate_profile_event(profiles[event.instrument_id], event, for_execution=True)
        except DataProfileError as exc:
            raise ForwardSessionRunnerError(f"instrument execution data profile rejected intent: {exc}") from exc
        if candidate_intent is not None and candidate_intent.get("symbol") != event.instrument_id:
            raise ForwardSessionRunnerError("paper quote instrument differs from the canonical candidate intent")
        if self._instrument_halted(session_id, event.instrument_id, simulated_at):
            raise ForwardSessionRunnerError("market instrument is halted at simulated execution time")
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
        execution_time = simulated_at.astimezone(timezone.utc)
        quote_age = execution_time - event.event_time.astimezone(timezone.utc)
        max_quote_age = self._max_quote_age(session_id)
        if quote_age < timedelta(0) or quote_age > timedelta(milliseconds=max_quote_age):
            current_mode = self.operating_modes.mode(session_id)
            if current_mode not in {OperatingMode.DEGRADED, OperatingMode.HALTED}:
                self.set_operating_mode(
                    session_id=session_id, mode=OperatingMode.DEGRADED,
                    event_id=f"stale-execution:{order_id}", occurred_at=execution_time,
                    reason=f"paper execution quote age {quote_age.total_seconds() * 1000:.3f}ms "
                           f"exceeds configured maximum {max_quote_age}ms")
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
        mode = self.operating_modes.mode(session_id)
        reduces_position = (side == "SELL" and int(accounting["positions"].get(event.instrument_id, 0)) >= quantity)
        if mode is not OperatingMode.ACTIVE and not reduces_position:
            raise ForwardSessionRunnerError(
                f"risk-increasing paper intent is blocked while operating mode is {mode.value}")
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
                   "as_of": signal_event.available_at.astimezone(timezone.utc).isoformat(),
                   "first_executable_after": timed.earliest_fill_time.isoformat(),
                   "expires_at": (timed.earliest_fill_time + timedelta(milliseconds=max_wait_ms)).isoformat()
                   if max_wait_ms is not None else None,
                   "last_good_quote_age_ms": quote_age.total_seconds() * 1000,
                   "decision_manifest_sha256": manifest_row[0],
                   "instrument_rulebook_sha256": rulebook.digest,
                   "effective_instrument_rule": effective_rule,
                   "limit_price": str(limit), "tick_size": str(tick), "freeze_limit": freeze_limit,
                   "slice_size": slice_size, "adverse_slippage_bps": str(slippage),
                   "max_wait_ms": max_wait_ms,
                   "price_band": [str(value) for value in band] if band is not None else None,
                   "execution_style": "PASSIVE_LIMIT" if passive_trade_event_ids else "MARKETABLE_LIMIT",
                   "execution_method": "PASSIVE_LIMIT" if passive_trade_event_ids else "MARKETABLE_LIMIT",
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
        fidelity = self._cost_fidelity_evidence(session_id, request, receipt, config, bucket)
        receipt["cost_fidelity"] = fidelity
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
        fidelity["cost_model_version"] = schedule.version
        fidelity["cost_model_id"] = schedule.schedule_id
        fidelity["cost_schedule_sha256"] = schedule.source_sha256
        fidelity["cost_schedule_source"] = schedule.source_ref
        fidelity["stress_scenario"] = config.constraints.get("cost_stress_scenario", "UNAVAILABLE")
        fidelity["unavailable_fields"] = [name for name, value in fidelity.items()
                                           if value == "UNAVAILABLE"]

    def _cost_fidelity_evidence(self, session_id: str, request: dict[str, Any], receipt: dict[str, Any],
                                config: Any, bucket: Any) -> dict[str, Any]:
        provenance = receipt.get("quote_provenance") or {}
        event_ids = request.get("quote_event_ids") or request.get("input_quote_event_ids") or []
        if not event_ids:
            event_ids = request.get("market_event_ids", [])
        quote_rows = []
        for event_id in event_ids:
            row = self._con.execute("SELECT event_json, event_sha256 FROM runner_events "
                                    "WHERE session_id=? AND event_id=? AND kind='quote'",
                                    [session_id, event_id]).fetchone()
            if row is not None:
                event = json.loads(row[0])
                quote_rows.append({"event_id": event_id, "sha256": row[1],
                                   "source": event.get("source", "UNAVAILABLE"),
                                   "event_time": event.get("event_time", "UNAVAILABLE"),
                                   "received_at": event.get("received_at", "UNAVAILABLE"),
                                   "available_at": event.get("available_at", "UNAVAILABLE")})
        if not quote_rows and isinstance(provenance, dict) and provenance.get("event_id"):
            quote_rows.append({"event_id": provenance["event_id"],
                               "sha256": provenance.get("event_sha256", "UNAVAILABLE"),
                               "source": provenance.get("source", "UNAVAILABLE"),
                               "event_time": provenance.get("event_time_utc", "UNAVAILABLE"),
                               "received_at": provenance.get("received_at_utc", "UNAVAILABLE"),
                               "available_at": provenance.get("available_at_utc", "UNAVAILABLE")})
        if not quote_rows:
            instrument_id = request.get("instrument_id") or request.get("symbol")
            receipt_clock = receipt.get("simulated_at")
            if instrument_id and receipt_clock:
                candidates = self._con.execute("""SELECT event_id, event_json, event_sha256 FROM runner_events
                    WHERE session_id=? AND kind='quote' AND available_at<=?
                    ORDER BY available_at DESC, event_id DESC""", [session_id, receipt_clock]).fetchall()
                for event_id, event_raw, digest in candidates:
                    event = json.loads(event_raw)
                    event_instrument = event.get("instrument_id")
                    normalized_matches = (isinstance(event_instrument, str)
                                          and isinstance(instrument_id, str)
                                          and event_instrument.split(":", 1)[-1] ==
                                          instrument_id.split(":", 1)[-1])
                    if event_instrument != instrument_id and not normalized_matches:
                        continue
                    quote_rows.append({"event_id": event_id, "sha256": digest,
                                       "source": event.get("source", "UNAVAILABLE"),
                                       "event_time": event.get("event_time", "UNAVAILABLE"),
                                       "received_at": event.get("received_at", "UNAVAILABLE"),
                                       "available_at": event.get("available_at", "UNAVAILABLE")})
                    break
        receipt_clock = receipt.get("simulated_at", "UNAVAILABLE")
        return {"schema": "fb.cost_fidelity.v1",
                "provider_clock": quote_rows[-1]["event_time"] if quote_rows else "UNAVAILABLE",
                "receipt_clock": receipt_clock,
                "quote_source": quote_rows[-1]["source"] if quote_rows else "UNAVAILABLE",
                "quote_event_ids": [row["event_id"] for row in quote_rows],
                "quote_sha256": [row["sha256"] for row in quote_rows],
                "quote_clocks": quote_rows,
                "liquidity_bucket": bucket or "UNAVAILABLE",
                "cost_model_version": "UNAVAILABLE", "cost_model_id": "UNAVAILABLE",
                "cost_schedule_sha256": "UNAVAILABLE", "cost_schedule_source": "UNAVAILABLE",
                "stress_scenario": config.constraints.get("cost_stress_scenario", "UNAVAILABLE"),
                "impact_calibration": "UNAVAILABLE",
                "data_rights_attestation": "UNAVAILABLE",
                "unavailable_fields": ["provider_clock", "quote_source", "quote_event_ids", "quote_sha256",
                                       "liquidity_bucket", "cost_model_version", "cost_model_id",
                                       "cost_schedule_sha256", "cost_schedule_source", "stress_scenario",
                                       "impact_calibration", "data_rights_attestation"]}

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

