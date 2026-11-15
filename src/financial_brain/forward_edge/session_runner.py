"""Durable normalized-event session runner foundation (PE-0017/0023)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
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
            available_at TEXT NOT NULL, event_sha256 TEXT NOT NULL, event_json TEXT NOT NULL,
            PRIMARY KEY(session_id, event_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_decisions (
            session_id TEXT NOT NULL, decision_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
            outcome TEXT NOT NULL, decided_at TEXT NOT NULL, record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL, PRIMARY KEY(session_id, decision_id))""")
        self._con.execute("""CREATE TABLE IF NOT EXISTS runner_session_reports (
            session_id TEXT PRIMARY KEY, status TEXT NOT NULL, report_sha256 TEXT NOT NULL,
            report_json TEXT NOT NULL)""")
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
        intents = [json.loads(row[0]) for row in self._con.execute(
            "SELECT request_json FROM paper_intents WHERE session_id=? ORDER BY order_id",
            [session_id]).fetchall()]
        receipts = [json.loads(row[0]) for row in self._con.execute(
            "SELECT receipt_json FROM paper_execution_receipts WHERE session_id=? ORDER BY order_id",
            [session_id]).fetchall()]
        return {"events": events, "decisions": decisions, "lifecycle": lifecycle,
                "intents": intents, "execution_receipts": receipts}

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
        if state is not SessionState.OPEN:
            raise ForwardSessionRunnerError("only an OPEN session can close")
        if complete:
            self._require_resolved_paper_intents(session_id)
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
            self._con.execute("INSERT INTO runner_events VALUES (?, ?, ?, ?, ?, ?)",
                              [session_id, event_id, kind, record["occurred_at"], digest, canonical])
            self._con.commit()

