"""Persistent event-driven paper-session coordinator.

The runner owns lifecycle and evidence ordering. Strategy, risk, and simulated execution
remain injected, deterministic adapters so replay and observed feeds use the same contract.
It has no broker interface and can never emit a physical order.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode


class RunnerError(ValueError):
    """Invalid session identity, transition, event order, or adapter result."""


class ExecutionOutcomeUnknown(RunnerError):
    """A durable outbox intent has no execution receipt and needs explicit reconciliation."""


STATE_MACHINE_VERSION = "forward-session-v1"
STATE_ORDER = ("PLANNED", "PREFLIGHT", "OPEN", "DRAINING", "CLOSED")
TERMINAL_STATES = frozenset({"CLOSED", "INCOMPLETE", "REJECTED"})
STATE_TRANSITIONS = {("PLANNED", "PREFLIGHT"), ("PREFLIGHT", "OPEN"), ("OPEN", "DRAINING"),
                     ("DRAINING", "CLOSED"), ("PREFLIGHT", "REJECTED"),
                     ("OPEN", "REJECTED"), ("DRAINING", "REJECTED"),
                     ("PLANNED", "INCOMPLETE"), ("PREFLIGHT", "INCOMPLETE"),
                     ("OPEN", "INCOMPLETE"), ("DRAINING", "INCOMPLETE")}


@dataclass(frozen=True)
class ForwardRunConfig:
    session_id: str
    exchange_date: str
    cohort_id: str
    strategy_sha256: str
    risk_policy_sha256: str
    constraints_sha256: str
    ledger_id: str
    benchmark_id: str
    instruments: tuple[str, ...]
    capital_inr: str
    mode: Mode = Mode.LIVE_PAPER

    def canonical(self) -> dict:
        if not all((self.session_id, self.exchange_date, self.cohort_id, self.benchmark_id,
                    self.capital_inr, self.instruments, self.ledger_id)):
            raise RunnerError("run identity, instruments, benchmark and capital are required")
        if len(set(self.instruments)) != len(self.instruments) or any(not i for i in self.instruments):
            raise RunnerError("instruments must be nonempty and unique")
        if self.mode not in (Mode.LIVE_PAPER, Mode.SHADOW, Mode.RESEARCH_REPLAY):
            raise RunnerError("forward runner only accepts replay, shadow, or paper mode")
        try:
            if date.fromisoformat(self.exchange_date).isoformat() != self.exchange_date:
                raise ValueError
            capital = Decimal(self.capital_inr)
            if not capital.is_finite() or capital <= 0:
                raise ValueError
        except (ValueError, InvalidOperation) as exc:
            raise RunnerError("exchange_date and capital must be canonical date and positive finite amount") from exc
        for name, value in (("strategy_sha256", self.strategy_sha256),
                            ("risk_policy_sha256", self.risk_policy_sha256),
                            ("constraints_sha256", self.constraints_sha256)):
            try:
                if len(value) != 64 or int(value, 16) < 0:
                    raise ValueError
            except ValueError as exc:
                raise RunnerError(f"{name} must be a SHA-256 hex digest") from exc
        return {"session_id": self.session_id, "exchange_date": self.exchange_date,
                "cohort_id": self.cohort_id, "strategy_sha256": self.strategy_sha256,
                "risk_policy_sha256": self.risk_policy_sha256,
                "constraints_sha256": self.constraints_sha256, "ledger_id": self.ledger_id,
                "benchmark_id": self.benchmark_id, "instruments": sorted(set(self.instruments)),
                "capital_inr": format(capital.normalize(), "f"), "mode": self.mode.value}

    @property
    def sha256(self) -> str:
        body = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


class ForwardSessionRunner:
    """Journal normalized events and adapter outcomes in a restartable session stream.

    ``decision_fn`` receives only events available by ``decision_at``. The execution adapter
    receives the complete archived session stream and a stable intent id; it must use only
    post-decision observations and be idempotent by that id. Results are journaled before return.
    """

    def __init__(self, journal_path: Path) -> None:
        self.journal_path = Path(journal_path)

    def mark_incomplete(self, session_id: str, reason: str) -> None:
        """Close a nonterminal session as incomplete without fabricating an outcome."""
        if not session_id or not reason.strip():
            raise RunnerError("session id and incomplete reason are required")
        journal = DurableJournal(self.journal_path)
        records = [r for r in journal.records() if r.get("session_id") == session_id]
        if not any(r.get("t") == "SESSION_PLAN" for r in records):
            raise RunnerError("cannot mark an unplanned session incomplete")
        if any(r.get("t") == "SESSION_CLOSED" for r in records):
            raise RunnerError("closed session cannot become incomplete")
        self._ensure_state(journal, session_id, "INCOMPLETE")
        journal.append({"t": "SESSION_INCOMPLETE", "session_id": session_id, "reason": reason.strip()})

    @staticmethod
    def _ensure_state(journal: DurableJournal, session_id: str, target: str) -> None:
        records = [r for r in journal.records() if r.get("session_id") == session_id]
        current = next((r["state"] for r in reversed(records) if r.get("t") == "SESSION_STATE"), None)
        if current is None:
            if target != "PLANNED":
                raise RunnerError("session state is missing its PLANNED record")
        elif current == target:
            return
        elif current in TERMINAL_STATES:
            # Recovery may finish writing the terminal result after a crash between the state
            # record and SESSION_CLOSED; the state itself remains terminal and is not changed.
            if target in STATE_ORDER:
                return
            raise RunnerError(f"cannot transition terminal session state {current}")
        elif (current, target) not in STATE_TRANSITIONS:
            # A replay after a crash may re-enter a phase already passed; never move state backward.
            if current in STATE_ORDER and target in STATE_ORDER and STATE_ORDER.index(current) > STATE_ORDER.index(target):
                return
            raise RunnerError(f"invalid session transition {current} -> {target}")
        journal.append({"t": "SESSION_STATE", "session_id": session_id,
                        "state_machine_version": STATE_MACHINE_VERSION, "state": target})

    def begin(self, config: ForwardRunConfig) -> None:
        """Create or resume the durable session before market polling starts."""
        plan = config.canonical()
        journal = DurableJournal(self.journal_path)
        records = journal.records()
        existing = [r for r in records if r.get("session_id") == config.session_id]
        if existing:
            planned = next((r for r in existing if r.get("t") == "SESSION_PLAN"), None)
            if planned is None or planned.get("config_sha256") != config.sha256:
                raise RunnerError("session id already exists with different or incomplete configuration")
            if any(r.get("t") == "SESSION_CLOSED" for r in existing):
                return
            if any(r.get("t") == "SESSION_STATE" and r.get("state") == "INCOMPLETE" for r in existing):
                raise RunnerError("incomplete session is terminal; use a new session id for another attempt")
        else:
            journal.append({"t": "SESSION_PLAN", "session_id": config.session_id,
                            "config": plan, "config_sha256": config.sha256})
        state_records = [r for r in existing if r.get("t") == "SESSION_STATE"]
        if not state_records:
            self._ensure_state(journal, config.session_id, "PLANNED")
        self._ensure_state(journal, config.session_id, "PREFLIGHT")
        self._ensure_state(journal, config.session_id, "OPEN")

    def append_events(self, config: ForwardRunConfig, events: Iterable[NormalizedEvent]) -> int:
        """Durably batch normalized source events during polling; returns the number newly stored."""
        self.begin(config)
        journal = DurableJournal(self.journal_path)
        records = journal.records()
        session_records = [r for r in records if r.get("session_id") == config.session_id]
        if any(r.get("t") == "SESSION_CLOSED" for r in session_records):
            raise RunnerError("cannot append market events to a closed session")
        state = next((r["state"] for r in reversed(session_records) if r.get("t") == "SESSION_STATE"), None)
        if state not in ("OPEN", "DRAINING"):
            raise RunnerError(f"market events require OPEN session state; got {state}")
        known = {r.get("event_sha256") for r in session_records if r.get("t") == "MARKET_EVENT"}
        allowed = set(config.canonical()["instruments"])
        additions = []
        for event in events:
            if event.instrument not in allowed:
                raise RunnerError(f"event instrument is outside the frozen universe: {event.instrument}")
            digest = event.content_sha256()
            if digest in known:
                continue
            if state != "OPEN":
                raise RunnerError("new events cannot be appended after a session starts draining")
            additions.append({"t": "MARKET_EVENT", "session_id": config.session_id,
                              "event_sha256": digest, "event": self._event_dict(event)})
            known.add(digest)
        journal.append_many(additions)
        return len(additions)

    def record_preflight(self, config: ForwardRunConfig, result: dict) -> None:
        """Persist one immutable, fail-closed preflight verdict before session decisions."""
        if not isinstance(result, dict) or type(result.get("approved")) is not bool:
            raise RunnerError("preflight result must contain a boolean approved field")
        try:
            json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RunnerError("preflight result must be finite JSON data") from exc
        self.begin(config)
        journal = DurableJournal(self.journal_path)
        rows = [r for r in journal.records() if r.get("session_id") == config.session_id]
        if any(r.get("t") in {"DECISION", "SESSION_CLOSED"} for r in rows):
            raise RunnerError("preflight must be recorded before the session decision")
        prior = next((r.get("result") for r in rows if r.get("t") == "PREFLIGHT_RESULT"), None)
        if prior is not None:
            if prior != result:
                raise RunnerError("session preflight is immutable")
            return
        journal.append({"t": "PREFLIGHT_RESULT", "session_id": config.session_id, "result": result})

    def record_data_rejections(self, config: ForwardRunConfig, rejections: Iterable[dict]) -> int:
        """Durably record source rows rejected before a decision, preserving their reason and source hash."""
        self.begin(config)
        journal = DurableJournal(self.journal_path)
        rows = [r for r in journal.records() if r.get("session_id") == config.session_id]
        if any(r.get("t") in {"DECISION", "SESSION_CLOSED"} for r in rows):
            raise RunnerError("data rejections must be recorded before the session decision")
        known = {r.get("event_sha256"): r for r in rows if r.get("t") == "DATA_REJECTION"}
        additions = []
        for rejected in rejections:
            if not isinstance(rejected, dict):
                raise RunnerError("each rejected-data record must be a dictionary")
            digest, reason = rejected.get("event_sha256"), rejected.get("reason")
            try:
                if not isinstance(digest, str) or len(digest) != 64 or len(bytes.fromhex(digest)) != 32:
                    raise ValueError
            except ValueError as exc:
                raise RunnerError("rejected-data event_sha256 must be a SHA-256 digest") from exc
            if not isinstance(reason, str) or not reason.strip():
                raise RunnerError("rejected-data reason is required")
            row = {"t": "DATA_REJECTION", "session_id": config.session_id,
                   "event_sha256": digest.lower(), "reason": reason.strip(),
                   "instrument": rejected.get("instrument")}
            try:
                json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise RunnerError("rejected-data record must be finite JSON data") from exc
            prior = known.get(row["event_sha256"])
            if prior is not None:
                if prior != row:
                    raise RunnerError("rejected event hash was reused with a different reason or instrument")
                continue
            known[row["event_sha256"]] = row
            additions.append(row)
        journal.append_many(additions)
        return len(additions)

    def run(self, config: ForwardRunConfig, events: Iterable[NormalizedEvent], *,
            decision_at: datetime,
            decision_fn: Callable[[list[NormalizedEvent]], dict],
            risk_fn: Callable[[dict], dict],
            execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict],
            reconcile_execution_fn: Callable[[str], dict] | None = None) -> dict:
        from financial_brain.forward_edge.market_data import decision_visible

        visible_at = self._utc(decision_at)
        self.begin(config)
        records = DurableJournal(self.journal_path).records()
        existing = [r for r in records if r.get("session_id") == config.session_id]
        prior_decision = next((r for r in reversed(existing) if r.get("t") == "DECISION"), None)
        if prior_decision is not None and prior_decision.get("decision_at_utc") != visible_at.isoformat():
            raise RunnerError("session id already has a different decision timestamp")
        closed = next((r for r in reversed(existing) if r.get("t") == "SESSION_CLOSED"), None)
        if closed is not None:
            return closed["result"]
        self.append_events(config, events)
        journal = DurableJournal(self.journal_path)
        records = journal.records()
        existing = [r for r in records if r.get("session_id") == config.session_id]
        if existing:
            planned = next((r for r in existing if r.get("t") == "SESSION_PLAN"), None)
            if planned is None or planned.get("config_sha256") != config.sha256:
                raise RunnerError("session id already exists with different or incomplete configuration")
            if any(r.get("t") == "SESSION_STATE" and r.get("state") == "INCOMPLETE" for r in existing):
                raise RunnerError("incomplete session is terminal; use a new session id for another attempt")
        old_events = [r["event"] for r in existing if r.get("t") == "MARKET_EVENT"]
        observed: list[NormalizedEvent] = [NormalizedEvent(
            kind=row["kind"], instrument=row["instrument"],
            event_utc=datetime.fromisoformat(row["event_utc"]),
            received_utc=datetime.fromisoformat(row["received_utc"]),
            available_utc=datetime.fromisoformat(row["available_utc"]),
            payload=tuple(tuple(item) for item in row["payload"])) for row in old_events]
        last_available = max((self._utc(e.available_utc) for e in observed), default=None)
        self._ensure_state(journal, config.session_id, "OPEN")
        observed.sort(key=lambda e: (self._utc(e.available_utc), e.content_sha256()))
        decision_events = [event for event in observed if decision_visible(event, visible_at)]

        preflight_record = next((r for r in existing if r.get("t") == "PREFLIGHT_RESULT"), None)
        if preflight_record is None:
            preflight = {"approved": False, "status": "MISSING", "reason": "PREFLIGHT_NOT_RECORDED"}
            journal.append({"t": "PREFLIGHT_RESULT", "session_id": config.session_id, "result": preflight})
        else:
            preflight = preflight_record["result"]
        if not isinstance(preflight, dict) or type(preflight.get("approved")) is not bool:
            raise RunnerError("persisted preflight must contain a boolean approved field")

        decision_record = next((r for r in reversed(existing) if r.get("t") == "DECISION"), None)
        decision = decision_record["decision"] if decision_record is not None else None
        if decision_record is not None and decision_record.get("decision_at_utc") != visible_at.isoformat():
            raise RunnerError("session id already has a different decision timestamp")
        if decision is None:
            decision = decision_fn(decision_events)
            if not isinstance(decision, dict) or decision.get("side") not in ("BUY", "SELL", "NONE", "NO_TRADE"):
                raise RunnerError("decision adapter must return a dictionary with BUY, SELL, or NONE side")
            journal.append({"t": "DECISION", "session_id": config.session_id,
                            "decision_at_utc": visible_at.isoformat(), "decision": decision})
        risk = next((r["risk"] for r in reversed(existing) if r.get("t") == "RISK_VERDICT"), None)
        if risk is None:
            risk = risk_fn(decision)
            if not isinstance(risk, dict) or type(risk.get("approved")) is not bool:
                raise RunnerError("risk adapter must return a dictionary with boolean approved")
            journal.append({"t": "RISK_VERDICT", "session_id": config.session_id, "risk": risk})
        if type(risk.get("approved")) is not bool:
            raise RunnerError("risk adapter must return a dictionary with boolean approved")
        self._ensure_state(journal, config.session_id, "DRAINING")
        result = {"session_id": config.session_id, "config_sha256": config.sha256,
                  "event_count": len(observed), "decision_event_count": len(decision_events),
                  "last_available_utc": last_available.isoformat()
                  if last_available else None, "decision": decision, "risk": risk,
                  "preflight": preflight,
                  "mode": config.mode.value, "physical_orders": False}
        if not risk["approved"]:
            self._ensure_state(journal, config.session_id, "REJECTED")
            result.update({"status": "REJECTED", "reason": str(risk.get("reason", "RISK_REJECTED"))})
        elif decision["side"] in ("BUY", "SELL") and not preflight["approved"]:
            self._ensure_state(journal, config.session_id, "REJECTED")
            result.update({"status": "REJECTED", "reason": str(preflight.get("reason", "PREFLIGHT_REJECTED"))})
        elif decision["side"] in ("NONE", "NO_TRADE"):
            result.update({"status": "NO_TRADE", "reason": str(decision.get("reason", "NO_SIGNAL"))})
        else:
            intent_id = hashlib.sha256(f"{config.session_id}:{config.sha256}".encode()).hexdigest()
            paper_intent = {"t": "PAPER_INTENT", "session_id": config.session_id,
                            "intent_id": intent_id, "config_sha256": config.sha256,
                            "decision": decision, "risk": risk,
                            "preflight": preflight}
            prior_intent = next((r for r in existing if r.get("t") == "PAPER_INTENT"
                                 and r.get("intent_id") == intent_id), None)
            if prior_intent is None:
                journal.append(paper_intent)
            elif prior_intent != paper_intent:
                raise RunnerError("stable intent id was reused with different decision or risk content")
            prior_execution = next((r["execution"] for r in reversed(existing)
                                    if r.get("t") == "PAPER_EXECUTION" and r.get("intent_id") == intent_id), None)
            has_prior_attempt = any(r.get("t") in {"OUTBOX_PENDING", "OUTBOX_UNKNOWN", "OUTBOX_RECONCILED"}
                                    and r.get("intent_id") == intent_id for r in existing)
            if prior_execution is None:
                if not has_prior_attempt:
                    journal.append({"t": "OUTBOX_PENDING", "session_id": config.session_id,
                                    "intent_id": intent_id})
                    execution = self._execute_with_receipt(journal, config.session_id, decision,
                                                           intent_id, observed, execution_fn)
                else:
                    if reconcile_execution_fn is None:
                        journal.append({"t": "OUTBOX_UNKNOWN", "session_id": config.session_id,
                                        "intent_id": intent_id, "reason": "RESTART_REQUIRES_RECONCILIATION"})
                        raise ExecutionOutcomeUnknown(
                            f"execution {intent_id} has no durable receipt; reconcile before retry or close")
                    reconciliation = reconcile_execution_fn(intent_id)
                    if not isinstance(reconciliation, dict) or reconciliation.get("status") not in {
                        "APPLIED", "NOT_APPLIED"
                    } or not reconciliation.get("reconciliation_id"):
                        raise RunnerError("execution reconciliation must identify APPLIED/NOT_APPLIED and a receipt")
                    state = reconciliation["status"]
                    journal.append({"t": "OUTBOX_RECONCILED", "session_id": config.session_id,
                                    "intent_id": intent_id, "status": state,
                                    "reconciliation_id": str(reconciliation["reconciliation_id"])})
                    if state == "APPLIED":
                        reconciled_execution = reconciliation.get("execution")
                        if not isinstance(reconciled_execution, dict):
                            raise RunnerError("APPLIED reconciliation must include a simulation receipt")
                        execution = reconciled_execution
                        self._validate_execution_receipt(execution)
                        self._persist_execution_and_ack(journal, config.session_id, intent_id, execution)
                    else:
                        execution = self._execute_with_receipt(journal, config.session_id, decision,
                                                               intent_id, observed, execution_fn)
            else:
                execution = prior_execution
                self._validate_execution_receipt(execution)
                if not any(r.get("t") == "OUTBOX_ACK" and r.get("intent_id") == intent_id for r in existing):
                    self._persist_ack(journal, config.session_id, intent_id, execution)
            result.update({"status": "PAPER_COMPLETE", "intent_id": intent_id, "execution": execution})
        result["closed_at_utc"] = datetime.now(timezone.utc).isoformat()
        if risk["approved"]:
            self._ensure_state(journal, config.session_id, "CLOSED")
        journal.append({"t": "SESSION_CLOSED", "session_id": config.session_id, "result": result})
        return result

    @classmethod
    def _execute_with_receipt(cls, journal: DurableJournal, session_id: str, decision: dict,
                              intent_id: str, events: list[NormalizedEvent],
                              execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict]) -> dict:
        try:
            execution = execution_fn(decision, intent_id, events)
            cls._validate_execution_receipt(execution)
        except Exception as exc:
            journal.append({"t": "OUTBOX_UNKNOWN", "session_id": session_id,
                            "intent_id": intent_id, "reason": type(exc).__name__})
            raise
        cls._persist_execution_and_ack(journal, session_id, intent_id, execution)
        return execution

    @staticmethod
    def _validate_execution_receipt(execution: object) -> None:
        if not isinstance(execution, dict):
            raise RunnerError("paper execution adapter must return a dictionary receipt")
        if not isinstance(execution.get("receipt_id"), str) or not execution["receipt_id"].strip():
            raise RunnerError("paper execution result needs a non-empty simulated receipt_id")
        if execution.get("fidelity") not in {"SIMULATED_QUOTE", "SIMULATED_BAR"}:
            raise RunnerError("paper execution receipt must declare simulated quote or bar fidelity")
        status = execution.get("status", execution.get("state"))
        if status not in {"FILLED", "PARTIAL", "MISSED", "REJECTED", "NO_TRADE"}:
            raise RunnerError("paper execution receipt needs a recognized outcome status")
        fills = execution.get("fills", [])
        if status in {"FILLED", "PARTIAL"}:
            if not isinstance(fills, list) or not fills:
                raise RunnerError("filled execution receipt needs one or more observed fills")
            for fill in fills:
                if not isinstance(fill, dict) or not isinstance(fill.get("fill_id"), str) or not fill["fill_id"].strip():
                    raise RunnerError("each simulated fill needs a stable fill_id")
                quantity = fill.get("quantity")
                if type(quantity) is not int or quantity <= 0:
                    raise RunnerError("simulated fill quantity must be a positive integer")
                try:
                    price = Decimal(str(fill.get("price")))
                except (InvalidOperation, ValueError) as exc:
                    raise RunnerError("simulated fill price must be positive and finite") from exc
                if not price.is_finite() or price <= 0:
                    raise RunnerError("simulated fill price must be positive and finite")
        elif fills not in (None, []):
            raise RunnerError("non-filled execution outcome cannot contain fills")
        try:
            json.dumps(execution, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RunnerError("paper execution receipt must be finite JSON data") from exc

    @classmethod
    def _persist_execution_and_ack(cls, journal: DurableJournal, session_id: str,
                                   intent_id: str, execution: dict) -> None:
        cls._validate_execution_receipt(execution)
        if not any(r.get("t") == "PAPER_EXECUTION" and r.get("intent_id") == intent_id
                   for r in journal.records()):
            journal.append({"t": "PAPER_EXECUTION", "session_id": session_id,
                            "intent_id": intent_id, "execution": execution})
        cls._persist_ack(journal, session_id, intent_id, execution)

    @staticmethod
    def _persist_ack(journal: DurableJournal, session_id: str, intent_id: str, execution: dict) -> None:
        records = journal.records()
        if any(r.get("t") == "OUTBOX_ACK" and r.get("intent_id") == intent_id for r in records):
            return
        digest = hashlib.sha256(json.dumps(
            execution, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        journal.append({"t": "OUTBOX_ACK", "session_id": session_id,
                        "intent_id": intent_id, "execution_sha256": digest,
                        "receipt_id": execution["receipt_id"]})

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise RunnerError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @classmethod
    def _event_dict(cls, event: NormalizedEvent) -> dict:
        return {"kind": event.kind, "instrument": event.instrument,
                "event_utc": cls._utc(event.event_utc).isoformat(),
                "received_utc": cls._utc(event.received_utc).isoformat(),
                "available_utc": cls._utc(event.available_utc).isoformat(),
                "payload": [list(item) for item in event.payload]}
