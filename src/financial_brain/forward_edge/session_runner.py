"""Persistent event-driven paper-session coordinator.

The runner owns lifecycle and evidence ordering. Strategy, risk, and simulated execution
remain injected, deterministic adapters so replay and observed feeds use the same contract.
It has no broker interface and can never emit a physical order.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import FeedHealth, NormalizedEvent, entry_allowed
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.net_alpha_policy import NetAlphaPolicyError, NetAlphaThresholdRegistry
from financial_brain.forward_edge.signal_seal import SignalSealError, seal_decision
from financial_brain.forward_edge.uncertainty_sizing import (UncertaintySizingError,
                                                              UncertaintySizingRegistry,
                                                              uncertainty_size_multiplier)
from financial_brain.forward_edge.forecast_cost_benefit import (CostBenefitError,
                                                               ForecastBenefitPolicyRegistry,
                                                               assess_cost_to_benefit)
from financial_brain.forward_edge.session_clock import ClockError, SessionClockProvider, as_utc
from financial_brain.forward_edge.session_ops import SessionDateLedger, SessionError


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
    attempt_id: str = "attempt-1"
    minimum_forecast_net_alpha: float = 0.0
    net_alpha_policy_sha256: str | None = None
    uncertainty_sizing_policy_sha256: str | None = None
    forecast_benefit_policy_sha256: str | None = None
    require_feed_health: bool = False

    def canonical(self) -> dict:
        if not all((self.session_id, self.exchange_date, self.cohort_id, self.benchmark_id,
                    self.capital_inr, self.instruments, self.ledger_id)):
            raise RunnerError("run identity, instruments, benchmark and capital are required")
        if (not isinstance(self.attempt_id, str) or not self.attempt_id
                or not self.attempt_id.strip()
                or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                       for ch in self.attempt_id)):
            raise RunnerError("attempt_id must be a nonempty stable token")
        if len(set(self.instruments)) != len(self.instruments) or any(not i for i in self.instruments):
            raise RunnerError("instruments must be nonempty and unique")
        if self.mode not in (Mode.LIVE_PAPER, Mode.SHADOW, Mode.RESEARCH_REPLAY):
            raise RunnerError("forward runner only accepts replay, shadow, or paper mode")
        if type(self.require_feed_health) is not bool:
            raise RunnerError("require_feed_health must be a boolean")
        if (isinstance(self.minimum_forecast_net_alpha, bool)
                or not isinstance(self.minimum_forecast_net_alpha, (int, float))
                or not math.isfinite(self.minimum_forecast_net_alpha)
                or self.minimum_forecast_net_alpha < 0):
            raise RunnerError("minimum forecast net-alpha hurdle must be finite and non-negative")
        if self.net_alpha_policy_sha256 is not None and (
                len(self.net_alpha_policy_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.net_alpha_policy_sha256)):
            raise RunnerError("net-alpha policy identity must be a SHA-256 digest")
        if self.uncertainty_sizing_policy_sha256 is not None and (
                len(self.uncertainty_sizing_policy_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.uncertainty_sizing_policy_sha256)):
            raise RunnerError("uncertainty sizing policy identity must be a SHA-256 digest")
        if self.forecast_benefit_policy_sha256 is not None and (
                len(self.forecast_benefit_policy_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.forecast_benefit_policy_sha256)):
            raise RunnerError("forecast-benefit policy identity must be a SHA-256 digest")
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
        canonical = {"session_id": self.session_id, "exchange_date": self.exchange_date,
                     "cohort_id": self.cohort_id, "strategy_sha256": self.strategy_sha256,
                     "risk_policy_sha256": self.risk_policy_sha256,
                     "constraints_sha256": self.constraints_sha256, "ledger_id": self.ledger_id,
                     "benchmark_id": self.benchmark_id, "instruments": sorted(set(self.instruments)),
                     "capital_inr": format(capital.normalize(), "f"), "mode": self.mode.value,
                     "minimum_forecast_net_alpha": self.minimum_forecast_net_alpha,
                     "net_alpha_policy_sha256": self.net_alpha_policy_sha256,
                     "session_key": self.session_key, "attempt_id": self.attempt_id}
        # Preserve historical identities for legacy runs without uncertainty policy provenance.
        if self.uncertainty_sizing_policy_sha256 is not None:
            canonical["uncertainty_sizing_policy_sha256"] = self.uncertainty_sizing_policy_sha256
        if self.forecast_benefit_policy_sha256 is not None:
            canonical["forecast_benefit_policy_sha256"] = self.forecast_benefit_policy_sha256
        if self.require_feed_health:
            canonical["require_feed_health"] = True
        return canonical

    @property
    def session_key(self) -> str:
        """Stable identity for one strategy/cohort/date/mode, shared by its immutable attempts."""
        body = json.dumps({"exchange_date": self.exchange_date, "cohort_id": self.cohort_id,
                           "strategy_sha256": self.strategy_sha256, "mode": self.mode.value},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

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

    def __init__(self, journal_path: Path,
                 clock_provider: SessionClockProvider | None = None,
                 session_date_ledger: SessionDateLedger | None = None,
                 net_alpha_threshold_registry: NetAlphaThresholdRegistry | None = None,
                 uncertainty_sizing_registry: UncertaintySizingRegistry | None = None,
                 forecast_benefit_registry: ForecastBenefitPolicyRegistry | None = None) -> None:
        self.journal_path = Path(journal_path)
        self.clock_provider = clock_provider or SessionClockProvider()
        self.session_date_ledger = session_date_ledger
        self.net_alpha_threshold_registry = net_alpha_threshold_registry
        self.uncertainty_sizing_registry = uncertainty_sizing_registry
        self.forecast_benefit_registry = forecast_benefit_registry

    def mark_incomplete(self, session_id: str, reason: str, *, evidence: dict | None = None) -> None:
        """Close a nonterminal session as incomplete without fabricating an outcome."""
        if not session_id or not reason.strip():
            raise RunnerError("session id and incomplete reason are required")
        journal = DurableJournal(self.journal_path)
        records = [r for r in journal.records() if r.get("session_id") == session_id]
        if not any(r.get("t") == "SESSION_PLAN" for r in records):
            raise RunnerError("cannot mark an unplanned session incomplete")
        if any(r.get("t") == "SESSION_CLOSED" for r in records):
            raise RunnerError("closed session cannot become incomplete")
        prior = next((r for r in records if r.get("t") == "SESSION_INCOMPLETE"), None)
        if prior is not None:
            if prior.get("reason") != reason.strip() or prior.get("evidence") != evidence:
                raise RunnerError("incomplete session reason is immutable")
            self._record_date_outcome("INCOMPLETE", reason.strip(), session_id)
            return
        self._ensure_state(journal, session_id, "INCOMPLETE")
        result = {"session_id": session_id, "status": "INCOMPLETE", "reason": reason.strip(),
                  "evidence": evidence, "physical_orders": False}
        journal.append({"t": "SESSION_INCOMPLETE", "session_id": session_id,
                        "reason": reason.strip(), "evidence": evidence, "result": result})
        self._record_date_outcome("INCOMPLETE", reason.strip(), session_id)

    def _record_date_outcome(self, status: str, reason: str, session_id: str) -> None:
        if self.session_date_ledger is None:
            return
        outcome = "NO_TRADE" if status in {"NO_TRADE", "REJECTED"} else (
            "CLOSED" if status == "PAPER_COMPLETE" else status)
        try:
            self.session_date_ledger.record_result(session_id, outcome, reason)
        except SessionError as exc:
            raise RunnerError(str(exc)) from exc

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
        if self.session_date_ledger is not None:
            outcome = self.session_date_ledger.outcome(config.session_id)
            if not self.session_date_ledger.is_planned(config.session_id):
                raise RunnerError("exchange date must be recorded in the session date ledger first")
            if outcome is not None and outcome.get("reason") == "NON_TRADING_DATE":
                raise RunnerError("cannot open a session on a non-trading date")
        journal = DurableJournal(self.journal_path)
        records = journal.records()
        if config.mode in (Mode.LIVE_PAPER, Mode.SHADOW):
            if self.net_alpha_threshold_registry is not None:
                if config.net_alpha_policy_sha256 is None:
                    raise RunnerError("paper session needs a frozen development-calibrated net-alpha policy")
                try:
                    policy = self.net_alpha_threshold_registry.resolve(config.strategy_sha256, config.cohort_id)
                except NetAlphaPolicyError as exc:
                    raise RunnerError(str(exc)) from exc
                if (policy is None or policy.sha256 != config.net_alpha_policy_sha256
                        or policy.minimum_net_alpha != config.minimum_forecast_net_alpha):
                    raise RunnerError("paper session threshold does not match its frozen cohort policy")
                if date.fromisoformat(config.exchange_date) < date.fromisoformat(policy.validation_start):
                    raise RunnerError("paper validation session predates the frozen policy validation start")
            elif config.net_alpha_policy_sha256 is not None:
                raise RunnerError("net-alpha policy identity cannot be verified without its registry")
            if self.uncertainty_sizing_registry is not None:
                if config.uncertainty_sizing_policy_sha256 is None:
                    raise RunnerError("paper session needs a frozen uncertainty-sizing policy")
                try:
                    sizing_policy = self.uncertainty_sizing_registry.resolve(
                        config.strategy_sha256, config.cohort_id)
                except UncertaintySizingError as exc:
                    raise RunnerError(str(exc)) from exc
                if sizing_policy is None or sizing_policy.sha256 != config.uncertainty_sizing_policy_sha256:
                    raise RunnerError("uncertainty sizing policy does not match the frozen strategy cohort")
                if date.fromisoformat(config.exchange_date) < date.fromisoformat(sizing_policy.validation_start):
                    raise RunnerError("paper session predates uncertainty-policy validation start")
            elif config.uncertainty_sizing_policy_sha256 is not None:
                raise RunnerError("uncertainty sizing policy cannot be verified without its registry")
            if self.forecast_benefit_registry is not None:
                if config.forecast_benefit_policy_sha256 is None:
                    raise RunnerError("paper session needs a frozen forecast-benefit calibration policy")
                try:
                    benefit_policy = self.forecast_benefit_registry.resolve(
                        config.strategy_sha256, config.cohort_id)
                except CostBenefitError as exc:
                    raise RunnerError(str(exc)) from exc
                if benefit_policy is None or benefit_policy.sha256 != config.forecast_benefit_policy_sha256:
                    raise RunnerError("forecast-benefit policy does not match the frozen strategy cohort")
                if date.fromisoformat(config.exchange_date) < date.fromisoformat(benefit_policy.validation_start):
                    raise RunnerError("paper session predates forecast-benefit validation start")
            elif config.forecast_benefit_policy_sha256 is not None:
                raise RunnerError("forecast-benefit policy cannot be verified without its registry")
        for row in records:
            if row.get("t") != "SESSION_PLAN" or row.get("session_id") == config.session_id:
                continue
            prior_config = row.get("config")
            if (isinstance(prior_config, dict)
                    and prior_config.get("strategy_sha256") == config.strategy_sha256
                    and prior_config.get("cohort_id") == config.cohort_id
                    and prior_config.get("mode") == config.mode.value
                    and (prior_config.get("minimum_forecast_net_alpha") != config.minimum_forecast_net_alpha
                         or prior_config.get("net_alpha_policy_sha256") != config.net_alpha_policy_sha256
                         or prior_config.get("uncertainty_sizing_policy_sha256")
                         != config.uncertainty_sizing_policy_sha256
                         or prior_config.get("forecast_benefit_policy_sha256")
                         != config.forecast_benefit_policy_sha256)):
                raise RunnerError("net-alpha hurdle is frozen for this strategy cohort")
        existing = [r for r in records if r.get("session_id") == config.session_id]
        for row in records:
            if row.get("t") != "SESSION_PLAN" or row.get("session_id") == config.session_id:
                continue
            prior_config = row.get("config")
            if (isinstance(prior_config, dict) and prior_config.get("session_key") == config.session_key
                    and prior_config.get("attempt_id") == config.attempt_id):
                raise RunnerError("attempt_id already exists for this strategy session key")
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

    def record_feed_health(self, config: ForwardRunConfig, health: FeedHealth, *, observed_at: datetime) -> None:
        """Persist one monotonic feed-health snapshot before the session decision."""
        observed = self._utc(observed_at)
        payload = {
            "observed_at_utc": observed.isoformat(),
            "last_available_utc": self._utc(health.last_available_utc).isoformat()
            if health.last_available_utc else None,
            "last_heartbeat_utc": self._utc(health.last_heartbeat_utc).isoformat()
            if health.last_heartbeat_utc else None,
            "gaps": health.gaps, "disconnects": health.disconnects,
            "dropped_updates": health.dropped_updates, "duplicates": health.duplicates,
            "reconnects": health.reconnects,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode("utf-8")).hexdigest()
        self.begin(config)
        journal = DurableJournal(self.journal_path)
        rows = [r for r in journal.records() if r.get("session_id") == config.session_id]
        if any(r.get("t") in {"DECISION", "SESSION_CLOSED"} for r in rows):
            raise RunnerError("feed health must be recorded before the session decision")
        samples = [r for r in rows if r.get("t") == "FEED_HEALTH"]
        if samples and observed < datetime.fromisoformat(samples[-1]["health"]["observed_at_utc"]):
            raise RunnerError("feed-health sample timestamps must be monotonic")
        if samples and samples[-1].get("snapshot_sha256") == digest:
            return
        journal.append({"t": "FEED_HEALTH", "session_id": config.session_id,
                        "snapshot_sha256": digest, "health": payload})

    def record_paper_order_update(self, config: ForwardRunConfig, intent_id: str, update: dict) -> int:
        """Durably append an exact paper-order update while its session is open.

        Execution adapters can call this from their update callback as fills and cancels
        arrive. Event IDs make retries idempotent; updates are never inferred from a final fill.
        """
        from financial_brain.forward_edge.paper_order_lifecycle import (
            ORDER_STATES, PaperOrderLifecycleError, normalize_order_update,
        )

        if not isinstance(intent_id, str) or not intent_id:
            raise RunnerError("paper order update requires the stable intent id")
        journal = DurableJournal(self.journal_path)
        records = [row for row in journal.records() if row.get("session_id") == config.session_id]
        if any(row.get("t") == "SESSION_CLOSED" for row in records):
            raise RunnerError("paper order update cannot follow session close")
        state = next((row.get("state") for row in reversed(records)
                      if row.get("t") == "SESSION_STATE"), None)
        if state not in {"OPEN", "DRAINING"}:
            raise RunnerError("paper order updates require an OPEN or DRAINING session")
        if not any(row.get("t") == "PAPER_INTENT" and row.get("intent_id") == intent_id
                   for row in records):
            raise RunnerError("paper order update must reference a durable paper intent")
        prior = [row for row in records if row.get("t") == "PAPER_ORDER_UPDATE"
                 and row.get("order_id") == intent_id]
        try:
            normalized = normalize_order_update(
                order_id=intent_id, update=update, update_index=len(prior),
                receipt_id=update.get("receipt_id"),
            )
        except PaperOrderLifecycleError as exc:
            raise RunnerError(f"invalid paper order update: {exc}") from exc
        normalized["session_id"] = config.session_id
        same_id = next((row for row in prior if row.get("update_id") == normalized["update_id"]), None)
        if same_id is not None:
            comparable = {key: value for key, value in same_id.items()
                          if key not in {"update_index", "update_sha256"}}
            candidate = {key: value for key, value in normalized.items()
                         if key not in {"update_index", "update_sha256"}}
            if comparable != candidate:
                raise RunnerError("paper order update identity was reused with different contents")
            return 0
        if prior:
            previous = prior[-1]
            transitions = {
                "CREATED": {"CREATED", "SUBMITTED", "CANCELED", "REJECTED", "NO_TRADE", "UNKNOWN"},
                "SUBMITTED": {"SUBMITTED", "PARTIAL", "FILLED", "CANCEL_PENDING", "CANCELED", "MISSED", "REJECTED", "NO_TRADE", "UNKNOWN"},
                "PARTIAL": {"PARTIAL", "FILLED", "CANCEL_PENDING", "CANCELED", "MISSED", "UNKNOWN"},
                "CANCEL_PENDING": {"CANCEL_PENDING", "PARTIAL", "FILLED", "CANCELED", "UNKNOWN"},
                "CANCELED": {"CANCELED", "PARTIAL", "FILLED"},
                "MISSED": {"MISSED"}, "FILLED": {"FILLED"},
                "REJECTED": {"REJECTED"}, "UNKNOWN": set(ORDER_STATES),
            }
            if normalized["status"] not in transitions.get(previous["status"], set()):
                raise RunnerError("paper order update violates the order state transition contract")
            if normalized["filled_quantity"] < previous["filled_quantity"]:
                raise RunnerError("cumulative paper fills cannot decrease")
            if normalized["requested_quantity"] != previous["requested_quantity"]:
                raise RunnerError("paper order requested quantity is immutable")
            if self._utc(datetime.fromisoformat(normalized["at_utc"])) < self._utc(
                    datetime.fromisoformat(previous["at_utc"])):
                raise RunnerError("paper order update timestamps must be monotonic")
        journal.append(normalized)
        return 1

    def run(self, config: ForwardRunConfig, events: Iterable[NormalizedEvent], *,
            decision_at: datetime | None = None,
            decision_fn: Callable[[list[NormalizedEvent]], dict],
            risk_fn: Callable[[dict], dict],
            execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict],
            reconcile_execution_fn: Callable[[str], dict] | None = None) -> dict:
        from financial_brain.forward_edge.market_data import decision_visible

        self.begin(config)
        journal = DurableJournal(self.journal_path)
        records = journal.records()
        existing = [r for r in records if r.get("session_id") == config.session_id]
        prior_decision = next((r for r in reversed(existing) if r.get("t") == "DECISION"), None)
        try:
            if prior_decision is not None:
                visible_at = self._utc(datetime.fromisoformat(prior_decision["decision_at_utc"]))
                if decision_at is not None and self._utc(decision_at) != visible_at:
                    raise RunnerError("session id already has a different decision timestamp")
            else:
                visible_at = self.clock_provider.decision_time(decision_at)
        except ClockError as exc:
            evidence = {"clock_trusted": False, "phase": "DECISION_CLOCK",
                        "clock_error_type": type(exc).__name__}
            self.mark_incomplete(config.session_id, "DECISION_CLOCK_FAILURE", evidence=evidence)
            raise RunnerError(f"decision clock failed; session marked incomplete: {exc}") from exc
        closed = next((r for r in reversed(existing) if r.get("t") == "SESSION_CLOSED"), None)
        if closed is not None:
            self._record_date_outcome(closed["result"]["status"], closed["result"].get("reason", "SESSION_CLOSED"),
                                      config.session_id)
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
            payload=tuple(tuple(item) for item in row["payload"]),
            ingested_utc=datetime.fromisoformat(row["ingested_utc"]) if row.get("ingested_utc") else None)
            for row in old_events]
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

        feed_health_record = next((r for r in reversed(existing) if r.get("t") == "FEED_HEALTH"), None)
        feed_health_facts = None
        feed_health_reasons: list[str] = []
        if feed_health_record is not None:
            feed_health_facts = feed_health_record["health"]
            health = FeedHealth(
                datetime.fromisoformat(feed_health_facts["last_available_utc"])
                if feed_health_facts.get("last_available_utc") else None,
                int(feed_health_facts["gaps"]), int(feed_health_facts["disconnects"]),
                int(feed_health_facts["dropped_updates"]), int(feed_health_facts["duplicates"]),
                int(feed_health_facts["reconnects"]),
                datetime.fromisoformat(feed_health_facts["last_heartbeat_utc"])
                if feed_health_facts.get("last_heartbeat_utc") else None,
            )
            allowed, feed_health_reasons = entry_allowed(health, visible_at)
        else:
            allowed = not config.require_feed_health
            if not allowed:
                feed_health_reasons = ["FEED_HEALTH_MISSING"]
        if not allowed:
            reason = "FEED_ENDED_EARLY" if any(code in feed_health_reasons for code in (
                "STALE_QUOTE", "STALE_HEARTBEAT", "NO_QUOTES", "FUTURE_QUOTE",
                "FUTURE_HEARTBEAT", "FEED_HEALTH_MISSING")) else "FEED_HEALTH_UNSAFE"
            evidence = {
                "feed_health_sha256": feed_health_record.get("snapshot_sha256")
                if feed_health_record else None,
                "feed_health": feed_health_facts,
                "blocking_reasons": feed_health_reasons,
                "last_available_utc": last_available.isoformat() if last_available else None,
                "entry_blocked": True,
            }
            self.mark_incomplete(config.session_id, reason, evidence=evidence)
            return {"session_id": config.session_id, "config_sha256": config.sha256,
                    "status": "INCOMPLETE", "reason": reason,
                    "event_count": len(observed), "decision_event_count": 0,
                    "feed_health": evidence, "physical_orders": False}
        decision_record = next((r for r in reversed(existing) if r.get("t") == "DECISION"), None)
        decision = decision_record["decision"] if decision_record is not None else None
        if decision_record is not None and decision_record.get("decision_at_utc") != visible_at.isoformat():
            raise RunnerError("session id already has a different decision timestamp")
        if decision is None:
            if not allowed:
                reason = "STALE_FEED" if any(r in feed_health_reasons for r in (
                    "STALE_QUOTE", "STALE_HEARTBEAT", "FUTURE_QUOTE", "FUTURE_HEARTBEAT", "NO_QUOTES")) \
                    else "FEED_HEALTH_UNSAFE"
                decision = {"side": "NONE", "reason": reason,
                            "feed_health_reasons": feed_health_reasons}
            else:
                decision = decision_fn(decision_events)
            if not isinstance(decision, dict) or decision.get("side") not in ("BUY", "SELL", "NONE", "NO_TRADE"):
                raise RunnerError("decision adapter must return a dictionary with BUY, SELL, or NONE side")
            decision = self._apply_forecast_cost_benefit(config, decision)
            decision = self._apply_uncertainty_sizing(config, decision)
            try:
                sealed = seal_decision(
                    session_id=config.session_id, strategy_sha256=config.strategy_sha256,
                    config_sha256=config.sha256, decision_at=visible_at, decision=decision,
                    inputs=decision_events, preflight=preflight,
                    feed_health_sha256=(feed_health_record.get("snapshot_sha256")
                                        if feed_health_record else None),
                )
            except SignalSealError as exc:
                raise RunnerError(f"decision cannot be sealed against its causal inputs: {exc}") from exc
            journal.append({"t": "DECISION", "session_id": config.session_id, **sealed})
        risk_record = next((r for r in reversed(existing) if r.get("t") == "RISK_VERDICT"), None)
        risk = risk_record["risk"] if risk_record is not None else None
        if risk is None:
            risk = risk_fn(decision)
            if not isinstance(risk, dict) or type(risk.get("approved")) is not bool:
                raise RunnerError("risk adapter must return a dictionary with boolean approved")
        if type(risk.get("approved")) is not bool:
            raise RunnerError("risk adapter must return a dictionary with boolean approved")
        gated_risk = self._apply_net_alpha_hurdle(config, decision, risk)
        if risk_record is None or gated_risk != risk:
            risk = gated_risk
            journal.append({"t": "RISK_VERDICT", "session_id": config.session_id, "risk": risk})
        hurdle = risk.get("net_alpha_hurdle", {})
        hurdle_no_trade = (risk.get("reason") == "NET_ALPHA_HURDLE_NOT_MET"
                           and hurdle.get("required") is True and hurdle.get("passed") is False)
        reported_decision = decision
        if hurdle_no_trade:
            reported_decision = {"side": "NO_TRADE", "reason": "NET_ALPHA_HURDLE_NOT_MET",
                                 "rejected_candidate": decision,
                                 "expected_net_alpha": hurdle.get("expected_net_alpha"),
                                 "net_alpha_forecast": hurdle.get("net_alpha_forecast")}
            disposition = {"t": "DECISION_DISPOSITION", "session_id": config.session_id,
                           "decision": reported_decision}
            prior_disposition = next((r for r in existing if r.get("t") == "DECISION_DISPOSITION"), None)
            if prior_disposition is None:
                journal.append(disposition)
            elif prior_disposition != disposition:
                raise RunnerError("stored net-alpha no-trade disposition conflicts with its decision")
        self._ensure_state(journal, config.session_id, "DRAINING")
        result = {"session_id": config.session_id, "config_sha256": config.sha256,
                  "event_count": len(observed), "decision_event_count": len(decision_events),
                  "last_available_utc": last_available.isoformat()
                  if last_available else None, "decision": reported_decision, "risk": risk,
                  "preflight": preflight,
                  "mode": config.mode.value, "physical_orders": False}
        if feed_health_facts is not None:
            result["feed_health"] = {**feed_health_facts, "entry_allowed": allowed,
                                     "blocking_reasons": feed_health_reasons}
        if hurdle_no_trade:
            result.update({"status": "NO_TRADE", "reason": "NET_ALPHA_HURDLE_NOT_MET"})
        elif not risk["approved"]:
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
            outbox_history = [r for r in existing
                              if r.get("t") in {"OUTBOX_PENDING", "OUTBOX_UNKNOWN", "OUTBOX_RECONCILED"}
                              and r.get("intent_id") == intent_id]
            if prior_execution is None:
                last_outbox = outbox_history[-1] if outbox_history else None
                if last_outbox is None:
                    journal.append({"t": "OUTBOX_PENDING", "session_id": config.session_id,
                                    "intent_id": intent_id})
                    execution = self._execute_with_receipt(journal, config.session_id,
                                                           self._execution_decision(decision, visible_at),
                                                           intent_id, observed, execution_fn,
                                                           self.clock_provider, visible_at)
                elif last_outbox.get("t") == "OUTBOX_RECONCILED" \
                        and last_outbox.get("status") == "NOT_APPLIED":
                    # The prior attempt was proven absent. A new durable PENDING marker
                    # lets recovery distinguish this retry from the prior reconciliation.
                    journal.append({"t": "OUTBOX_PENDING", "session_id": config.session_id,
                                    "intent_id": intent_id})
                    execution = self._execute_with_receipt(journal, config.session_id,
                                                           self._execution_decision(decision, visible_at),
                                                           intent_id, observed, execution_fn,
                                                           self.clock_provider, visible_at)
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
                    reconciliation_id = str(reconciliation["reconciliation_id"])
                    if last_outbox.get("t") == "OUTBOX_RECONCILED":
                        if (state != last_outbox.get("status")
                                or reconciliation_id != last_outbox.get("reconciliation_id")):
                            raise RunnerError("recovered reconciliation contradicts its durable receipt")
                    else:
                        journal.append({"t": "OUTBOX_RECONCILED", "session_id": config.session_id,
                                        "intent_id": intent_id, "status": state,
                                        "reconciliation_id": reconciliation_id})
                    if state == "APPLIED":
                        reconciled_execution = reconciliation.get("execution")
                        if not isinstance(reconciled_execution, dict):
                            raise RunnerError("APPLIED reconciliation must include a simulation receipt")
                        execution = reconciled_execution
                        self._validate_execution_receipt(execution)
                        self._persist_execution_and_ack(journal, config.session_id, intent_id, execution)
                    else:
                        journal.append({"t": "OUTBOX_PENDING", "session_id": config.session_id,
                                        "intent_id": intent_id})
                        execution = self._execute_with_receipt(journal, config.session_id,
                                                               self._execution_decision(decision, visible_at),
                                                               intent_id, observed, execution_fn,
                                                               self.clock_provider, visible_at)
            else:
                execution = prior_execution
                self._validate_execution_receipt(execution)
                if not any(r.get("t") == "OUTBOX_ACK" and r.get("intent_id") == intent_id for r in existing):
                    self._persist_ack(journal, config.session_id, intent_id, execution)
            result.update({"status": "PAPER_COMPLETE", "intent_id": intent_id, "execution": execution})
            # Preserve adapter-reported working/partial/cancel/missed events and exact
            # quantity reconciliation before this session is closed. When the adapter
            # only supplies a terminal receipt, unknown requested quantity remains null.
            from financial_brain.forward_edge.paper_order_lifecycle import (
                PaperOrderLifecycleError, updates_for_receipt,
            )
            try:
                order_updates = updates_for_receipt(
                    order_id=intent_id, decision=decision, receipt=execution,
                )
            except PaperOrderLifecycleError as exc:
                raise RunnerError(f"invalid paper order lifecycle receipt: {exc}") from exc
            for update in order_updates:
                self.record_paper_order_update(config, intent_id, update)
            result["order_updates"] = order_updates
            # The adapter may have streamed order updates through this runner while
            # executing. Refresh the journal cursor before appending the final skip/close.
            journal = DurableJournal(self.journal_path)
        execution_receipt = result.get("execution")
        execution_status = (execution_receipt.get("status", execution_receipt.get("state"))
                            if isinstance(execution_receipt, dict) else None)
        skip_needed = (result.get("status") in {"NO_TRADE", "REJECTED"}
                       or execution_status in {"NO_TRADE", "REJECTED", "MISSED", "CANCELED"})
        if skip_needed:
            reasons: list[str] = []

            def add_reason(value: object) -> None:
                if isinstance(value, str) and value.strip() and value not in reasons:
                    reasons.append(value.strip())

            add_reason(result.get("reason"))
            add_reason(decision.get("reason"))
            if risk.get("approved") is not True:
                add_reason(risk.get("reason", "RISK_REJECTED"))
            if preflight.get("approved") is not True:
                add_reason(preflight.get("reason", "PREFLIGHT_REJECTED"))
            if isinstance(risk.get("reasons"), list):
                for reason in risk["reasons"]:
                    add_reason(reason)
            if isinstance(risk.get("checks"), list):
                for check in risk["checks"]:
                    if isinstance(check, dict) and check.get("passed") is False:
                        add_reason(check.get("reason") or check.get("name"))
            if isinstance(execution_receipt, dict):
                add_reason(execution_receipt.get("reason"))
                if execution_status:
                    add_reason(f"EXECUTION_{execution_status}")
            if not reasons:
                reasons.append("PAPER_INTENT_WITHHELD")
            persisted_decision = next((row for row in reversed(journal.records())
                                       if row.get("session_id") == config.session_id
                                       and row.get("t") == "DECISION"), {})
            candidate_intent_id = (hashlib.sha256(f"{config.session_id}:{config.sha256}".encode()).hexdigest()
                                   if decision.get("side") in {"BUY", "SELL"} else None)
            skip_body = {
                "t": "PAPER_SKIP", "session_id": config.session_id,
                "attempt_id": config.attempt_id, "config_sha256": config.sha256,
                "candidate_intent_id": candidate_intent_id,
                "candidate_sha256": hashlib.sha256(json.dumps(
                    decision, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                "decision_sha256": persisted_decision.get("decision_sha256"),
                "signal_sha256": persisted_decision.get("signal_sha256"),
                "input_manifest_sha256": persisted_decision.get("input_manifest_sha256"),
                "preflight_sha256": persisted_decision.get("preflight_sha256"),
                "runner_status": result.get("status"),
                "execution_status": execution_status,
                "reason_codes": reasons,
                "decision": decision, "risk_verdict": risk, "preflight": preflight,
                "execution_summary": ({"status": execution_status,
                                       "reason": execution_receipt.get("reason")}
                                      if isinstance(execution_receipt, dict) else None),
            }
            skip_id = hashlib.sha256(json.dumps(skip_body, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False).encode()).hexdigest()
            skip_record = {**skip_body, "skip_id": skip_id}
            prior_skip = next((row for row in journal.records()
                               if row.get("session_id") == config.session_id and row.get("t") == "PAPER_SKIP"), None)
            if prior_skip is not None and prior_skip != skip_record:
                raise RunnerError("paper skip record is immutable for this session attempt")
            if prior_skip is None:
                journal.append(skip_record)
            result["skip_id"] = skip_id
            result["skip_reason_codes"] = reasons
        result["opportunity_coverage"] = _opportunity_coverage(
            exchange_date=config.exchange_date, planned_instruments=config.instruments,
            events=observed, decision=decision, execution=result.get("execution"),
        )
        result["closed_at_utc"] = datetime.now(timezone.utc).isoformat()
        if risk["approved"] or hurdle_no_trade:
            self._ensure_state(journal, config.session_id, "CLOSED")
        journal.append({"t": "SESSION_CLOSED", "session_id": config.session_id, "result": result})
        self._record_date_outcome(str(result["status"]), str(result.get("reason", "SESSION_CLOSED")),
                                  config.session_id)
        return result

    @staticmethod
    def _apply_net_alpha_hurdle(config: ForwardRunConfig, decision: dict, risk: dict) -> dict:
        """Require recomputable, costed positive net alpha before adding paper or shadow risk."""
        if config.mode not in (Mode.LIVE_PAPER, Mode.SHADOW):
            return risk
        side = decision.get("side")
        effect = decision.get("position_effect")
        if effect is None and side == "BUY":
            effect = "INCREASE"
        elif effect is None and side in ("NONE", "NO_TRADE"):
            effect = "REDUCE"
        if effect not in {"INCREASE", "REDUCE", "CLOSE"}:
            gate = {"required": True, "passed": False, "reason": "INVALID_POSITION_EFFECT"}
        elif effect != "INCREASE":
            gate = {"required": False, "passed": True, "reason": "POSITION_REDUCTION",
                    "position_effect": effect}
        else:
            raw_alpha = decision.get("expected_net_alpha")
            forecast_value = decision.get("net_alpha_forecast")
            forecast = forecast_value if isinstance(forecast_value, dict) else {}
            horizon = forecast.get("horizon")
            costs_included = decision.get("forecast_costs_included") is True
            forecast_fields = ("expected_return", "benchmark", "fees", "spread", "impact")
            valid_forecast = (isinstance(forecast_value, dict)
                              and all(isinstance(forecast.get(key), (int, float))
                                      and not isinstance(forecast.get(key), bool)
                                      and math.isfinite(float(forecast[key]))
                                      for key in forecast_fields)
                              and all(float(forecast[key]) >= 0 for key in ("fees", "spread", "impact")))
            computed_alpha = (float(forecast["expected_return"] - forecast["benchmark"]
                                    - forecast["fees"] - forecast["spread"] - forecast["impact"])
                              if valid_forecast else None)
            raw_alpha_value = (float(raw_alpha) if isinstance(raw_alpha, (int, float))
                               and not isinstance(raw_alpha, bool) and math.isfinite(float(raw_alpha)) else None)
            valid_alpha = (raw_alpha_value is not None and computed_alpha is not None
                           and math.isclose(raw_alpha_value, computed_alpha, rel_tol=1e-9, abs_tol=1e-12))
            decision_horizon = decision.get("forecast_horizon_sessions")
            valid_horizon = (isinstance(horizon, int) and not isinstance(horizon, bool) and horizon > 0
                             and decision_horizon == horizon)
            passed = bool(valid_alpha and valid_horizon and costs_included and computed_alpha is not None
                          and computed_alpha > config.minimum_forecast_net_alpha)
            gate = {"required": True, "passed": passed,
                    "reason": "NET_ALPHA_ABOVE_HURDLE" if passed else "NET_ALPHA_HURDLE_NOT_MET",
                    "expected_net_alpha": computed_alpha if valid_alpha else None,
                    "net_alpha_forecast": forecast if valid_forecast else None,
                    "forecast_horizon_sessions": horizon if valid_horizon else None,
                    "forecast_costs_included": costs_included,
                    "minimum_forecast_net_alpha": config.minimum_forecast_net_alpha}
        out = {**risk, "net_alpha_hurdle": gate}
        if not gate["passed"] and risk.get("approved") is True:
            out["approved"] = False
            out["reason"] = gate["reason"]
        return out

    def _apply_uncertainty_sizing(self, config: ForwardRunConfig, decision: dict) -> dict:
        """Scale paper-entry quantity by a frozen uncertainty policy or abstain on missing evidence."""
        if (config.mode not in (Mode.LIVE_PAPER, Mode.SHADOW)
                or self.uncertainty_sizing_registry is None):
            return decision
        effect = decision.get("position_effect")
        if effect is None and decision.get("side") == "BUY":
            effect = "INCREASE"
        if effect != "INCREASE":
            return decision
        try:
            policy = self.uncertainty_sizing_registry.resolve(config.strategy_sha256, config.cohort_id)
        except UncertaintySizingError as exc:
            raise RunnerError(str(exc)) from exc
        if policy is None or policy.sha256 != config.uncertainty_sizing_policy_sha256:
            raise RunnerError("paper entry uncertainty policy is unavailable or changed")
        alpha = decision.get("expected_net_alpha")
        uncertainty = decision.get("uncertainty_standard_error")
        quantity = decision.get("quantity")
        if (isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha)
                or isinstance(uncertainty, bool) or not isinstance(uncertainty, (int, float))
                or not math.isfinite(uncertainty) or uncertainty < 0
                or type(quantity) is not int or quantity <= 0):
            return {"side": "NO_TRADE", "reason": "UNCERTAINTY_SIZING_EVIDENCE_MISSING",
                    "rejected_candidate": decision,
                    "uncertainty_sizing": {"policy_sha256": policy.sha256, "passed": False}}
        try:
            multiplier = uncertainty_size_multiplier(expected_net_alpha=float(alpha),
                                                     uncertainty_standard_error=float(uncertainty),
                                                     policy=policy)
        except UncertaintySizingError as exc:
            raise RunnerError(str(exc)) from exc
        sized_quantity = math.floor(quantity * multiplier)
        receipt = {"policy_sha256": policy.sha256, "passed": sized_quantity > 0,
                   "raw_quantity": quantity, "uncertainty_standard_error": float(uncertainty),
                   "size_multiplier": multiplier, "sized_quantity": sized_quantity}
        if sized_quantity < 1:
            return {"side": "NO_TRADE", "reason": "UNCERTAINTY_SIZE_BELOW_ONE_UNIT",
                    "rejected_candidate": decision, "uncertainty_sizing": receipt}
        return {**decision, "quantity": sized_quantity, "uncertainty_sizing": receipt}

    def _apply_forecast_cost_benefit(self, config: ForwardRunConfig, decision: dict) -> dict:
        """Require a matching frozen benefit-calibration policy before a configured paper entry."""
        if (config.mode not in (Mode.LIVE_PAPER, Mode.SHADOW) or self.forecast_benefit_registry is None):
            return decision
        effect = decision.get("position_effect")
        if effect is None and decision.get("side") == "BUY":
            effect = "INCREASE"
        if effect != "INCREASE":
            return decision
        try:
            policy = self.forecast_benefit_registry.resolve(config.strategy_sha256, config.cohort_id)
        except CostBenefitError as exc:
            raise RunnerError(str(exc)) from exc
        if policy is None or policy.sha256 != config.forecast_benefit_policy_sha256:
            raise RunnerError("paper entry forecast-benefit policy is unavailable or changed")
        forecast = decision.get("net_alpha_forecast")
        if not isinstance(forecast, dict):
            return {"side": "NO_TRADE", "reason": "FORECAST_BENEFIT_EVIDENCE_MISSING",
                    "rejected_candidate": decision,
                    "cost_benefit_assessment": {"approved": False, "reason": "FORECAST_BENEFIT_UNCALIBRATED",
                                                "policy_sha256": policy.sha256}}
        try:
            expected = float(forecast["expected_return"])
            benchmark = float(forecast["benchmark"])
            fees, spread, impact = (float(forecast[name]) for name in ("fees", "spread", "impact"))
            model_sha = str(decision["forecast_model_sha256"])
            assessment = assess_cost_to_benefit(
                forecast_benefit=expected - benchmark, fees=fees, spread=spread, impact=impact,
                forecast_model_sha256=model_sha, session_date=date.fromisoformat(config.exchange_date),
                policy=policy, strategy_sha256=config.strategy_sha256, cohort_id=config.cohort_id)
        except (KeyError, TypeError, ValueError, CostBenefitError) as exc:
            return {"side": "NO_TRADE", "reason": "FORECAST_BENEFIT_EVIDENCE_MISSING",
                    "rejected_candidate": decision,
                    "cost_benefit_assessment": {"approved": False, "reason": str(exc),
                                                "policy_sha256": policy.sha256}}
        if not assessment["approved"]:
            return {"side": "NO_TRADE", "reason": str(assessment["reason"]),
                    "rejected_candidate": decision, "cost_benefit_assessment": assessment}
        calibrated_value = assessment["calibrated_benefit"]
        if isinstance(calibrated_value, bool) or not isinstance(calibrated_value, (int, float)):
            raise RunnerError("approved forecast-benefit assessment lacks a finite calibrated benefit")
        calibrated_benefit = float(calibrated_value)
        adjusted_forecast = {**forecast, "expected_return": benchmark + calibrated_benefit}
        adjusted_alpha = calibrated_benefit - fees - spread - impact
        return {**decision, "expected_net_alpha": adjusted_alpha,
                "uncalibrated_net_alpha_forecast": forecast,
                "net_alpha_forecast": adjusted_forecast,
                "cost_benefit_assessment": assessment}

    @classmethod
    def _execute_with_receipt(cls, journal: DurableJournal, session_id: str, decision: dict,
                              intent_id: str, events: list[NormalizedEvent],
                              execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict],
                              clock_provider: SessionClockProvider, decision_utc: datetime) -> dict:
        try:
            execution = execution_fn(decision, intent_id, events)
            if isinstance(execution, dict):
                execution = dict(execution)
                execution_at = clock_provider.simulated_execution_time(
                    decision_utc, execution.get("simulated_execution_at_utc"))
                execution["simulated_execution_at_utc"] = execution_at.isoformat()
            cls._validate_execution_receipt(execution)
        except ClockError as exc:
            journal = DurableJournal(journal.path)
            journal.append({"t": "OUTBOX_UNKNOWN", "session_id": session_id,
                            "intent_id": intent_id, "reason": "INVALID_SIMULATION_CLOCK"})
            raise RunnerError(str(exc)) from exc
        except Exception as exc:
            journal = DurableJournal(journal.path)
            journal.append({"t": "OUTBOX_UNKNOWN", "session_id": session_id,
                            "intent_id": intent_id, "reason": type(exc).__name__})
            raise
        journal = DurableJournal(journal.path)
        cls._persist_execution_and_ack(journal, session_id, intent_id, execution)
        return execution

    @staticmethod
    def _execution_decision(decision: dict, decision_at: datetime) -> dict:
        """Pass the immutable runner decision time to execution without changing the signal."""
        return {**decision, "decision_at_utc": decision_at.isoformat()}

    @staticmethod
    def _validate_execution_receipt(execution: object) -> None:
        if not isinstance(execution, dict):
            raise RunnerError("paper execution adapter must return a dictionary receipt")
        if not isinstance(execution.get("receipt_id"), str) or not execution["receipt_id"].strip():
            raise RunnerError("paper execution result needs a non-empty simulated receipt_id")
        if execution.get("fidelity") not in {"SIMULATED_QUOTE", "SIMULATED_BAR"}:
            raise RunnerError("paper execution receipt must declare simulated quote or bar fidelity")
        try:
            as_utc(datetime.fromisoformat(execution["simulated_execution_at_utc"]),
                   "simulated execution clock")
        except (KeyError, TypeError, ValueError) as exc:
            raise RunnerError("paper execution receipt needs an aware simulated execution timestamp") from exc
        status = execution.get("status", execution.get("state"))
        if status not in {"FILLED", "PARTIAL", "MISSED", "REJECTED", "NO_TRADE", "CANCELED"}:
            raise RunnerError("paper execution receipt needs a recognized outcome status")
        fills = execution.get("fills", [])
        if status in {"FILLED", "PARTIAL", "CANCELED"}:
            if not isinstance(fills, list) or (status in {"FILLED", "PARTIAL"} and not fills):
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
        result = {"kind": event.kind, "instrument": event.instrument,
                "event_utc": cls._utc(event.event_utc).isoformat(),
                "received_utc": cls._utc(event.received_utc).isoformat(),
                "available_utc": cls._utc(event.available_utc).isoformat(),
                "payload": [list(item) for item in event.payload]}
        if event.ingested_utc is not None:
            result["ingested_utc"] = cls._utc(event.ingested_utc).isoformat()
        return result


def _opportunity_coverage(*, exchange_date: str, planned_instruments: Iterable[str],
                          events: list[NormalizedEvent], decision: dict,
                          execution: object) -> dict:
    """Report session denominators without imputing unknown candidates or notionals."""
    planned = sorted(set(planned_instruments))
    quotes = [event for event in events if event.kind == "QUOTE"]
    observed_instruments = {event.instrument for event in quotes}
    execution = execution if isinstance(execution, dict) else {}
    quote_inputs = execution.get("quote_inputs", [])
    quote_inputs = quote_inputs if isinstance(quote_inputs, list) else []
    accepted_hashes = {row.get("event_sha256") for row in quote_inputs if isinstance(row, dict)}
    quality_rejections = execution.get("quote_quality_rejections", [])
    quality_rejections = quality_rejections if isinstance(quality_rejections, list) else []
    rejected_hashes = {row.get("event_sha256") for row in quality_rejections if isinstance(row, dict)}
    quote_by_hash = {event.content_sha256(): event for event in quotes}
    accepted_events = [quote_by_hash[digest] for digest in accepted_hashes if digest in quote_by_hash]
    candidate_rows = decision.get("candidate_signals")
    if isinstance(candidate_rows, list):
        candidates = [row for row in candidate_rows if isinstance(row, dict)]
    elif decision.get("side") in {"BUY", "SELL"}:
        candidates = [decision]
    else:
        candidates = []
    candidate_instruments = [str(row.get("instrument")) for row in candidates if row.get("instrument")]
    accepted_instruments = {event.instrument for event in accepted_events}
    covered_candidates = sum(1 for instrument in candidate_instruments if instrument in accepted_instruments)

    notional_values: list[Decimal] = []
    missing_candidate_notional = 0
    for candidate in candidates:
        value = candidate.get("candidate_notional_inr", candidate.get("notional_inr"))
        try:
            notional = Decimal(str(value))
        except (InvalidOperation, ValueError):
            missing_candidate_notional += 1
            continue
        if not notional.is_finite() or notional < 0:
            missing_candidate_notional += 1
            continue
        notional_values.append(notional)
    total_notional = sum(notional_values, Decimal(0))
    fills = execution.get("fills", [])
    fills = fills if isinstance(fills, list) else []
    filled_notional = Decimal(0)
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        try:
            amount = Decimal(str(fill["price"])) * int(fill["quantity"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if amount.is_finite() and amount >= 0:
            filled_notional += amount
    execution_status = execution.get("status", execution.get("state"))
    has_fill = bool(fills)
    missed_candidates = len(candidates) if execution_status in {"MISSED", "NO_TRADE"} and not has_fill else 0
    return {
        "exchange_date": exchange_date,
        "session_date_denominator": 1,
        "planned_instrument_count": len(planned),
        "quoted_instrument_count": len(observed_instruments.intersection(planned)),
        "missing_quote_instruments": sorted(set(planned) - observed_instruments),
        "quote_event_count": len(quotes),
        "candidate_signal_count": len(candidates),
        "candidate_signal_quote_covered_count": covered_candidates,
        "candidate_signal_quote_coverage_pct": round(100 * covered_candidates / len(candidates), 4)
        if candidates else None,
        "candidate_notional_denominator_inr": str(total_notional)
        if candidates and not missing_candidate_notional else None,
        "candidate_notional_missing_count": missing_candidate_notional,
        "filled_notional_inr": str(filled_notional) if has_fill else "0",
        "quote_inputs_accepted_count": len(accepted_hashes),
        "quote_inputs_quality_rejected_count": len(rejected_hashes),
        "filled_quote_count": 1 if has_fill and execution.get("used_quote_input_id") else 0,
        "missed_candidate_signal_count": missed_candidates,
        "lost_opportunity_denominator_count": len(candidates),
        "candidate_universe_scope": "STRATEGY_OUTPUT_NOT_MARKET_WIDE",
        "execution_status": execution_status,
        "missing_denominators": (["CANDIDATE_NOTIONAL_INR"] if missing_candidate_notional else [])
        + (["EXECUTION_QUOTE_RECEIPTS"] if candidates and not quote_inputs and not quality_rejections else []),
    }
