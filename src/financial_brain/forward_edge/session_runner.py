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


@dataclass(frozen=True)
class ForwardRunConfig:
    session_id: str
    exchange_date: str
    cohort_id: str
    strategy_sha256: str
    benchmark_id: str
    instruments: tuple[str, ...]
    capital_inr: str
    mode: Mode = Mode.LIVE_PAPER

    def canonical(self) -> dict:
        if not all((self.session_id, self.exchange_date, self.cohort_id, self.benchmark_id,
                    self.capital_inr, self.instruments)):
            raise RunnerError("run identity, instruments, benchmark and capital are required")
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
        try:
            if len(self.strategy_sha256) != 64 or int(self.strategy_sha256, 16) < 0:
                raise ValueError
        except ValueError as exc:
            raise RunnerError("strategy_sha256 must be a SHA-256 hex digest") from exc
        return {"session_id": self.session_id, "exchange_date": self.exchange_date,
                "cohort_id": self.cohort_id, "strategy_sha256": self.strategy_sha256,
                "benchmark_id": self.benchmark_id, "instruments": sorted(set(self.instruments)),
                "capital_inr": self.capital_inr, "mode": self.mode.value}

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

    def run(self, config: ForwardRunConfig, events: Iterable[NormalizedEvent], *,
            decision_at: datetime,
            decision_fn: Callable[[list[NormalizedEvent]], dict],
            risk_fn: Callable[[dict], dict],
            execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict]) -> dict:
        from financial_brain.forward_edge.market_data import decision_visible

        plan = config.canonical()
        journal = DurableJournal(self.journal_path)
        records = journal.records()
        existing = [r for r in records if r.get("session_id") == config.session_id]
        if existing:
            planned = next((r for r in existing if r.get("t") == "SESSION_PLAN"), None)
            if planned is None or planned.get("config_sha256") != config.sha256:
                raise RunnerError("session id already exists with different or incomplete configuration")
            closed = next((r for r in reversed(existing) if r.get("t") == "SESSION_CLOSED"), None)
            if closed is not None:
                return closed["result"]
        else:
            journal.append({"t": "SESSION_PLAN", "session_id": config.session_id,
                            "config": plan, "config_sha256": config.sha256})

        visible_at = self._utc(decision_at)
        old_events = [r["event"] for r in existing if r.get("t") == "MARKET_EVENT"]
        prior_hashes = {r.get("event_sha256") for r in existing if r.get("t") == "MARKET_EVENT"}
        observed: list[NormalizedEvent] = [NormalizedEvent(
            kind=row["kind"], instrument=row["instrument"],
            event_utc=datetime.fromisoformat(row["event_utc"]),
            received_utc=datetime.fromisoformat(row["received_utc"]),
            available_utc=datetime.fromisoformat(row["available_utc"]),
            payload=tuple(tuple(item) for item in row["payload"])) for row in old_events]
        last_available = max((self._utc(e.available_utc) for e in observed), default=None)
        for event in sorted(events, key=lambda e: (self._utc(e.available_utc), e.content_sha256())):
            if event.instrument not in plan["instruments"]:
                continue
            digest = event.content_sha256()
            if digest in prior_hashes:
                continue
            journal.append({"t": "MARKET_EVENT", "session_id": config.session_id,
                            "event_sha256": digest, "event": self._event_dict(event)})
            prior_hashes.add(digest)
            observed.append(event)
            last_available = self._utc(event.available_utc)
        decision_events = [event for event in observed if decision_visible(event, visible_at)]

        decision = next((r["decision"] for r in reversed(existing) if r.get("t") == "DECISION"), None)
        if decision is None:
            decision = decision_fn(decision_events)
            if not isinstance(decision, dict):
                raise RunnerError("decision adapter must return a dictionary")
            journal.append({"t": "DECISION", "session_id": config.session_id,
                            "decision_at_utc": visible_at.isoformat(), "decision": decision})
        risk = next((r["risk"] for r in reversed(existing) if r.get("t") == "RISK_VERDICT"), None)
        if risk is None:
            risk = risk_fn(decision)
            if not isinstance(risk, dict) or "approved" not in risk:
                raise RunnerError("risk adapter must return a dictionary with approved")
            journal.append({"t": "RISK_VERDICT", "session_id": config.session_id, "risk": risk})
        result = {"session_id": config.session_id, "config_sha256": config.sha256,
                  "event_count": len(observed), "decision_event_count": len(decision_events),
                  "last_available_utc": last_available.isoformat()
                  if last_available else None, "decision": decision, "risk": risk,
                  "mode": config.mode.value, "physical_orders": False}
        if not risk["approved"]:
            result.update({"status": "NO_TRADE", "reason": str(risk.get("reason", "RISK_REJECTED"))})
        else:
            intent_id = hashlib.sha256(f"{config.session_id}:{config.sha256}".encode()).hexdigest()
            prior_execution = next((r["execution"] for r in reversed(existing)
                                    if r.get("t") == "PAPER_EXECUTION"), None)
            if prior_execution is None:
                if not any(r.get("t") == "OUTBOX_PENDING" and r.get("intent_id") == intent_id
                           for r in existing):
                    journal.append({"t": "OUTBOX_PENDING", "session_id": config.session_id,
                                    "intent_id": intent_id})
                execution = execution_fn(decision, intent_id, observed)
            else:
                execution = prior_execution
            if not isinstance(execution, dict):
                raise RunnerError("paper execution adapter must return a dictionary")
            if prior_execution is None:
                journal.append({"t": "PAPER_EXECUTION", "session_id": config.session_id,
                                "intent_id": intent_id, "execution": execution})
                journal.append({"t": "OUTBOX_ACK", "session_id": config.session_id,
                                "intent_id": intent_id, "execution_sha256": hashlib.sha256(
                                    json.dumps(execution, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
            result.update({"status": "PAPER_COMPLETE", "intent_id": intent_id, "execution": execution})
        result["closed_at_utc"] = datetime.now(timezone.utc).isoformat()
        journal.append({"t": "SESSION_CLOSED", "session_id": config.session_id, "result": result})
        return result

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
