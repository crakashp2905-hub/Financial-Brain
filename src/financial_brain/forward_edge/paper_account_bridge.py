"""Idempotently post forward-session simulation receipts to the cash-equity paper account."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import Callable, Iterable

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.fixed_look_registry import FixedLookRegistry
from financial_brain.forward_edge.holding_period_schedule import (
    HoldingScheduleError, scheduled_exit_order_intents,
)
from financial_brain.forward_edge.market_data import FeedHealth, NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.pretrade_gates import (
    PreTradeGateError,
    PreTradePolicy,
    PreTradeSnapshot,
    evaluate_pretrade,
)
from financial_brain.forward_edge.registry import Registry, StrategySpec, order_intent_allowed
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError
from financial_brain.kernel.schemas import (
    AssetClass,
    EventSide,
    FillEvent,
    InstrumentRef,
    IntentEvent,
    MarkEvent,
    OrderEvent,
    OrderStatus,
    TradingEvent,
    canonical_event_payload,
)
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError
from financial_brain.kernel.paper_authority import (
    PaperExecutionAuthorityRegistry, configured_paper_authority_path,
)
from financial_brain.kernel.registered_paper_books import (
    PaperBookRegistryError,
    RegisteredPaperBooks,
)


class PaperAccountBridgeError(ValueError):
    """The durable paper receipt cannot be reconciled to the cash-equity account."""


class ForwardPaperSessionService:
    """Run explicit preflight, durable forward session, and idempotent account reconciliation."""

    def __init__(self, runner: ForwardSessionRunner, account: PaperAccount, *,
                 strategy_spec: StrategySpec, registry: Registry,
                 paper_books: RegisteredPaperBooks, paper_book_plan_id: str,
                 paper_account_root: Path,
                 authority_registry: PaperExecutionAuthorityRegistry | None = None) -> None:
        if not isinstance(paper_books, RegisteredPaperBooks) or not paper_book_plan_id:
            raise PaperAccountBridgeError("LIVE_PAPER requires a registered frozen paper-book plan")
        self.runner = runner
        self.account = account
        self.strategy_spec = strategy_spec
        self.registry = registry
        self.paper_books = paper_books
        self.paper_book_plan_id = paper_book_plan_id
        self.paper_account_root = Path(paper_account_root).resolve()
        self.authority_registry = authority_registry or PaperExecutionAuthorityRegistry(
            configured_paper_authority_path(self.paper_account_root.parent))
        self.runner.authority_registry = self.authority_registry

    def _validate_strategy(self, config: ForwardRunConfig, decision_at: datetime):
        """Bind the paper service to the exact guarded registry identity before any session writes."""
        if config.mode is not Mode.LIVE_PAPER:
            raise PaperAccountBridgeError("paper account service accepts LIVE_PAPER sessions only")
        _require_distinct_authority_journals(self.runner.journal_path, self.account.path)
        try:
            plan, allocation = self.paper_books.resolve(
                strategy_id=self.strategy_spec.strategy_id, at=decision_at)
        except PaperBookRegistryError as exc:
            raise PaperAccountBridgeError(f"no registered paper-book allocation for session: {exc}") from exc
        if plan.plan_id != self.paper_book_plan_id:
            raise PaperAccountBridgeError("session does not use the configured frozen paper-book plan")
        try:
            expected_account_path = self.paper_books.account_path(
                plan_id=self.paper_book_plan_id, account_id=allocation.account_id,
                root=self.paper_account_root)
        except PaperBookRegistryError as exc:
            raise PaperAccountBridgeError(f"registered paper-account path is unavailable: {exc}") from exc
        if (self.account.account_id != allocation.account_id
                or self.account.initial_cash != allocation.capital_inr
                or self.account.path.resolve() != expected_account_path):
            raise PaperAccountBridgeError(
                "paper account ID, journal path, and opening capital must match the frozen strategy allocation")
        try:
            registered = self.registry.get(self.strategy_spec.strategy_id)
        except KeyError as exc:
            raise PaperAccountBridgeError("paper strategy is not registered") from exc
        if registered != self.strategy_spec or not order_intent_allowed(self.strategy_spec, self.registry):
            raise PaperAccountBridgeError("paper strategy is not the exact registered paper-capable version")
        if config.authority_strategy_id != self.strategy_spec.strategy_id:
            raise PaperAccountBridgeError("session authority strategy ID does not match the registered strategy")
        if config.authority_account_id != allocation.account_id:
            raise PaperAccountBridgeError("session authority account ID does not match the registered book allocation")
        if config.strategy_sha256 != self.strategy_spec.code_sha256:
            raise PaperAccountBridgeError("session strategy digest does not match the registered strategy")
        if config.benchmark_id != self.strategy_spec.benchmark_id:
            raise PaperAccountBridgeError("session benchmark does not match the frozen strategy benchmark")
        return allocation

    def run(self, config: ForwardRunConfig, events: Iterable[NormalizedEvent], *,
            preflight_fn: Callable[[], dict], decision_fn: Callable[[list[NormalizedEvent]], dict],
            risk_fn: Callable[[dict], dict], execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict],
            decision_at: datetime | None = None,
            reconcile_execution_fn: Callable[[str], dict] | None = None,
            feed_health: FeedHealth | None = None,
            feed_health_observed_at: datetime | None = None,
            fixed_look_registry: FixedLookRegistry | None = None,
            fixed_look_policy_id: str | None = None,
            fixed_look_observation_fn: Callable[[], dict] | None = None,
            pretrade_policy: PreTradePolicy | None = None,
            pretrade_snapshot_fn: Callable[[ForwardRunConfig, dict, list[NormalizedEvent], datetime],
                                           PreTradeSnapshot] | None = None) -> dict:
        """Persist one preflight, run the session, then reconcile its receipt to PaperAccount."""
        if (pretrade_policy is None) != (pretrade_snapshot_fn is None):
            raise PaperAccountBridgeError("pre-trade policy requires a matching snapshot adapter")
        if (config.pretrade_policy_sha256 is None) != (pretrade_policy is None):
            raise PaperAccountBridgeError("frozen session pre-trade policy and runtime policy must match")
        if pretrade_policy is not None:
            if pretrade_policy.sha256 != config.pretrade_policy_sha256:
                raise PaperAccountBridgeError("runtime pre-trade policy differs from the session-bound policy")
            if pretrade_policy.strategy_sha256 != config.strategy_sha256:
                raise PaperAccountBridgeError("pre-trade policy does not match the frozen strategy digest")
            if pretrade_policy.strategy_version != str(self.strategy_spec.version):
                raise PaperAccountBridgeError("pre-trade policy does not match the registered strategy version")
        if decision_at is None:
            existing_decision = next((row for row in DurableJournal(self.runner.journal_path).records()
                                      if row.get("t") == "DECISION"
                                      and row.get("session_id") == config.session_id), None)
            decision_at = (datetime.fromisoformat(existing_decision["decision_at_utc"])
                           if existing_decision is not None
                           else self.runner.clock_provider.decision_time())
        allocation = self._validate_strategy(config, decision_at)
        if not config.require_feed_health:
            raise PaperAccountBridgeError("LIVE_PAPER account service requires feed-health gating")
        prior_feed_health = []
        if self.runner.journal_path.exists():
            prior_feed_health = [row for row in DurableJournal(self.runner.journal_path).records()
                                 if row.get("session_id") == config.session_id
                                 and row.get("t") == "FEED_HEALTH"]
        if not prior_feed_health and (feed_health is None or feed_health_observed_at is None):
            raise PaperAccountBridgeError(
                "LIVE_PAPER account service requires a feed-health snapshot before preflight")
        if (feed_health is None) != (feed_health_observed_at is None):
            raise PaperAccountBridgeError("feed-health snapshot and observed time must be supplied together")
        if feed_health is not None:
            if not isinstance(feed_health, FeedHealth):
                raise PaperAccountBridgeError("feed-health snapshot must be a validated FeedHealth value")
        try:
            self.authority_registry.claim(
                strategy_id=self.strategy_spec.strategy_id,
                exchange_date=config.exchange_date,
                engine_id="forward_paper",
                account_id=allocation.account_id,
            )
        except ValueError as exc:
            raise PaperAccountBridgeError(f"paper execution authority conflict: {exc}") from exc
        if feed_health is not None:
            if feed_health_observed_at is None:
                raise PaperAccountBridgeError("feed-health observed time is required")
            try:
                self.runner.record_feed_health(config, feed_health, observed_at=feed_health_observed_at)
            except RunnerError as exc:
                raise PaperAccountBridgeError(f"invalid paper feed-health snapshot: {exc}") from exc
        existing_preflight = next((row for row in DurableJournal(self.runner.journal_path).records()
                                   if row.get("t") == "PREFLIGHT_RESULT"
                                   and row.get("session_id") == config.session_id), None)
        if existing_preflight is None:
            try:
                preflight = preflight_fn()
            except Exception as exc:  # noqa: BLE001 - fail closed while retaining only exception type
                preflight = {"approved": False, "reason": "PREFLIGHT_CALLBACK_FAILED",
                             "error_type": type(exc).__name__}
            if not isinstance(preflight, dict) or type(preflight.get("approved")) is not bool:
                preflight = {"approved": False, "reason": "PREFLIGHT_CALLBACK_INVALID"}
            self.runner.record_preflight(config, preflight)
        risk_stop_report = None
        risk_stop_block: str | None = None
        registered_risk_configured = any(value is not None for value in (
            fixed_look_registry, fixed_look_policy_id, fixed_look_observation_fn))
        if registered_risk_configured and (
                fixed_look_registry is None or fixed_look_policy_id is None
                or fixed_look_observation_fn is None):
            risk_stop_block = "FIXED_LOOK_CONFIGURATION_INVALID"
        elif (fixed_look_registry is not None and fixed_look_policy_id is not None
              and fixed_look_observation_fn is not None):
            try:
                observation = fixed_look_observation_fn()
                candidate = fixed_look_registry.evaluate(fixed_look_policy_id, observation)
            except Exception as exc:  # noqa: BLE001 - evidence callback failures block orders
                candidate = None
                risk_stop_block = f"FIXED_LOOK_REGISTRY_EVALUATION_FAILED:{type(exc).__name__}"
        else:
            candidate = None
        if registered_risk_configured:
            if risk_stop_block is None:
                required = {"status", "orders_permission", "completed_sessions", "policy_id", "policy_sha256"}
                try:
                    json.dumps(candidate, sort_keys=True, allow_nan=False)
                except (TypeError, ValueError):
                    risk_stop_block = "FIXED_LOOK_EVIDENCE_INVALID"
                if risk_stop_block is None and (not isinstance(candidate, dict) or not required <= set(candidate)
                        or not isinstance(candidate.get("status"), str)
                        or not isinstance(candidate.get("orders_permission"), str)
                        or type(candidate.get("completed_sessions")) is not int
                        or candidate["completed_sessions"] < 1
                        or not isinstance(candidate.get("policy_id"), str)
                        or not candidate["policy_id"].strip()
                        or not isinstance(candidate.get("policy_sha256"), str)
                        or len(candidate["policy_sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in candidate["policy_sha256"])):
                    risk_stop_block = "FIXED_LOOK_EVIDENCE_INVALID"
                elif risk_stop_block is None and isinstance(candidate, dict):
                    risk_stop_report = candidate
                    permission = candidate["orders_permission"]
                    status = candidate["status"]
                    if permission == "DISABLED_FOR_RISK_HARM" and status == "STOP_FOR_RISK_HARM":
                        incident_material = (f"{candidate['policy_sha256']}:{candidate['completed_sessions']}:"
                                             f"{decision_at.isoformat()}")
                        incident_id = hashlib.sha256(incident_material.encode("utf-8")).hexdigest()
                        reason = f"FIXED_LOOK_RISK_HARM:{candidate['policy_id']}"
                        try:
                            self.account.trip_risk_latch(
                                incident_id=incident_id, reason=reason, occurred_at=decision_at)
                            risk_stop_block = "FIXED_LOOK_RISK_HARM"
                        except PaperAccountError:
                            risk_stop_block = "PAPER_ACCOUNT_RISK_LATCHED"
                    elif permission == "NOT_AUTHORIZED":
                        risk_stop_block = "FIXED_LOOK_RISK_EVIDENCE_MISSING"
                    elif permission != "UNCHANGED_BY_THIS_REPORT" or status == "STOP_FOR_RISK_HARM":
                        risk_stop_block = "FIXED_LOOK_EVIDENCE_INVALID"
                elif risk_stop_block is None:
                    risk_stop_block = "FIXED_LOOK_EVIDENCE_INVALID"

        due_exit_decision: dict | None = None

        def guarded_decision(observed: list[NormalizedEvent]) -> dict:
            if self.account.risk_latched or risk_stop_block is not None:
                return {"side": "NONE", "reason": risk_stop_block or "PAPER_ACCOUNT_RISK_LATCHED"}
            if due_exit_decision is not None:
                return due_exit_decision
            return decision_fn(observed)

        def guarded_risk(decision: dict) -> dict:
            if self.account.risk_latched or risk_stop_block is not None:
                result = {"approved": False,
                          "reason": risk_stop_block or "PAPER_ACCOUNT_RISK_LATCHED"}
                if risk_stop_report is not None:
                    result["fixed_look_risk_stop"] = risk_stop_report
                return result
            base_risk = risk_fn(decision)
            if (decision.get("side") == "SELL"
                    and decision.get("position_effect") in {"REDUCE", "CLOSE"}):
                if not isinstance(base_risk, dict) or type(base_risk.get("approved")) is not bool:
                    return base_risk
                return {**base_risk, "pretrade_gate": {
                    "required": False, "approved": True, "reason": "POSITION_REDUCTION"}}
            if pretrade_policy is None or pretrade_snapshot_fn is None or decision.get("side") not in {"BUY", "SELL"}:
                return base_risk
            try:
                visible = [event for event in captured_events if event.available_utc <= decision_at]
                snapshot = pretrade_snapshot_fn(config, decision, visible, decision_at)
                if not isinstance(snapshot, PreTradeSnapshot):
                    raise PreTradeGateError("snapshot adapter must return a validated PreTradeSnapshot")
                visible_quote_hashes = {event.content_sha256() for event in visible}
                if (snapshot.session_id != config.session_id
                        or snapshot.strategy_sha256 != config.strategy_sha256
                        or snapshot.instrument not in config.instruments
                        or snapshot.instrument != decision.get("instrument")
                        or snapshot.side != decision.get("side")
                        or snapshot.quantity != decision.get("quantity")
                        or snapshot.quote is None
                        or snapshot.quote.content_sha256() not in visible_quote_hashes
                        or not isinstance(snapshot.decision_at, datetime)
                        or snapshot.decision_at.tzinfo is None
                        or snapshot.decision_at.utcoffset() is None
                        or snapshot.decision_at.astimezone(timezone.utc)
                        != decision_at.astimezone(timezone.utc)):
                    raise PreTradeGateError("pre-trade snapshot identity differs from the sealed decision")
                gate = evaluate_pretrade(pretrade_policy, snapshot).as_risk_verdict()
            except Exception as exc:  # noqa: BLE001 - failed pre-trade evidence must block the intent
                return {
                    "approved": False,
                    "reason": "PRETRADE_EVIDENCE_INVALID",
                    "pretrade_policy_sha256": pretrade_policy.sha256,
                    "pretrade_error_type": type(exc).__name__,
                }
            if not isinstance(base_risk, dict) or type(base_risk.get("approved")) is not bool:
                return base_risk
            result = {**base_risk, "pretrade_gate": gate,
                      "approved": base_risk["approved"] and gate["approved"]}
            if not gate["approved"]:
                result["reason"] = gate["reason"]
                result["reasons"] = list(gate["reasons"])
            return result

        captured_events = list(events)
        try:
            due_exits = scheduled_exit_order_intents(
                [event for event in captured_events if event.kind == "BOOK_POSITION"],
                exchange_session=date.fromisoformat(config.exchange_date),
            )
        except (HoldingScheduleError, ValueError) as exc:
            raise PaperAccountBridgeError(f"scheduled paper exit evidence is invalid: {exc}") from exc
        if len(due_exits) > 1:
            raise PaperAccountBridgeError(
                "the single-intent paper session cannot route multiple due exits; split the closes into separate sessions")
        if due_exits:
            due = due_exits[0]
            if due["instrument"] not in config.instruments:
                raise PaperAccountBridgeError("scheduled exit instrument is outside the frozen session universe")
            position = self.account.position(due["instrument"])
            if position.quantity != due["quantity"]:
                raise PaperAccountBridgeError(
                    "scheduled exit quantity does not match the registered paper-account inventory")
            due_hash = due["position_identity"]
            visible_position_hashes = {
                event.content_sha256() for event in captured_events
                if event.kind == "BOOK_POSITION" and event.available_utc <= decision_at
            }
            matching_events = [
                event for event in captured_events
                if event.kind == "BOOK_POSITION"
                and dict(event.payload).get("position_state") in {"EXIT_DUE", "EXIT_OVERDUE"}
                and dict(event.payload).get("entry_day") == due_hash["entry_day"]
                and event.instrument == due["instrument"]
            ]
            if not matching_events or any(event.content_sha256() not in visible_position_hashes
                                          for event in matching_events):
                raise PaperAccountBridgeError(
                    "scheduled exit position evidence was not available by the frozen decision time")
            due_exit_decision = {
                "side": due["side"], "instrument": due["instrument"],
                "quantity": due["quantity"], "position_effect": due["position_effect"],
                "reason": due["reason"], "scheduled_exit_order_id": due["order_id"],
                "scheduled_exit_session": due["scheduled_exit_session"],
                "exit_window": due["exit_window"], "exit_calendar_id": due["calendar_id"],
                "exit_calendar_source_sha256": due["calendar_source_sha256"],
            }
        execution_adapter = execution_fn
        bind_updates = getattr(execution_fn, "bind_order_update_callback", None)
        if callable(bind_updates):
            execution_adapter = bind_updates(
                lambda intent_id, update: self.runner.record_paper_order_update(
                    config, intent_id, update))
        result = self.runner.run(
            config, captured_events, decision_at=decision_at,
            decision_fn=guarded_decision, risk_fn=guarded_risk, execution_fn=execution_adapter,
            reconcile_execution_fn=reconcile_execution_fn,
        )
        if result.get("status") == "PAPER_COMPLETE":
            reconciled_events = _persisted_session_events(
                self.runner.journal_path, config.session_id)
            evidence = reconcile_paper_account(
                runner_journal_path=self.runner.journal_path, config=config, result=result,
                account=self.account, events=reconciled_events, paper_books=self.paper_books,
                paper_book_plan_id=self.paper_book_plan_id,
                strategy_id=self.strategy_spec.strategy_id,
            )
            return {**result, "account_reconciliation": evidence}
        return result


def _persisted_session_events(journal_path: Path, session_id: str) -> list[NormalizedEvent]:
    """Rebuild the session event stream from its durable runner journal after restart."""
    rows = [row for row in DurableJournal(Path(journal_path)).records()
            if row.get("t") == "MARKET_EVENT" and row.get("session_id") == session_id]
    events: dict[str, NormalizedEvent] = {}
    try:
        for row in rows:
            value = row["event"]
            event = NormalizedEvent(
                kind=value["kind"], instrument=value["instrument"],
                event_utc=datetime.fromisoformat(value["event_utc"]),
                received_utc=datetime.fromisoformat(value["received_utc"]),
                available_utc=datetime.fromisoformat(value["available_utc"]),
                payload=tuple(tuple(item) for item in value["payload"]),
                ingested_utc=datetime.fromisoformat(value["ingested_utc"])
                if value.get("ingested_utc") else None,
            )
            digest = event.content_sha256()
            if digest != row.get("event_sha256"):
                raise ValueError("event digest differs from its journal record")
            events[digest] = event
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperAccountBridgeError("persisted session event journal cannot be replayed") from exc
    return sorted(events.values(), key=lambda event: (event.available_utc, event.content_sha256()))


def reconcile_paper_account(*, runner_journal_path: Path, config: ForwardRunConfig,
                            result: dict, account: PaperAccount,
                            events: list[NormalizedEvent], paper_books: RegisteredPaperBooks,
                            paper_book_plan_id: str, strategy_id: str) -> dict:
    """Post a closed runner receipt to PaperAccount, then persist cross-journal reconciliation.

    Stable intent/fill IDs make replays safe after a crash between an account event and its
    reconciliation marker. BUY decisions must carry an explicit cash reserve in the risk
    verdict; the bridge never invents capital allocation. A fee schedule is mandatory.
    """
    if account.fee_schedule is None:
        raise PaperAccountBridgeError("paper account needs an effective-dated fee schedule")
    _require_distinct_authority_journals(runner_journal_path, account.path)
    if result.get("session_id") != config.session_id or result.get("status") != "PAPER_COMPLETE":
        raise PaperAccountBridgeError("only a completed, matching paper session can be reconciled")
    intent_id = result.get("intent_id")
    receipt = result.get("execution")
    decision = result.get("decision")
    risk = result.get("risk")
    if not isinstance(intent_id, str) or not isinstance(receipt, dict) or not isinstance(decision, dict):
        raise PaperAccountBridgeError("paper session is missing its stable intent, execution receipt, or decision")
    evidence_fields = ("execution_model_id", "execution_model_sha256", "fee_schedule_sha256",
                      "latency_model_sha256", "data_snapshot_sha256")
    if any(not isinstance(receipt.get(key), str) or not receipt[key] for key in evidence_fields):
        raise PaperAccountBridgeError("execution receipt is missing immutable model, fee, latency or snapshot identity")
    for key in evidence_fields[1:]:
        if len(receipt[key]) != 64 or any(char not in "0123456789abcdef" for char in receipt[key]):
            raise PaperAccountBridgeError(f"execution receipt has an invalid {key}")
    if receipt["fee_schedule_sha256"] != account.fee_schedule.sha256:
        raise PaperAccountBridgeError("execution receipt fee hash does not match the paper account schedule")
    quote_hashes = receipt.get("quote_event_sha256s")
    if (not isinstance(quote_hashes, list)
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value) for value in quote_hashes)):
        raise PaperAccountBridgeError("execution receipt quote evidence must be a list of SHA-256 hashes")
    snapshot_digest = hashlib.sha256(json.dumps(quote_hashes, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode("utf-8")).hexdigest()
    if snapshot_digest != receipt["data_snapshot_sha256"]:
        raise PaperAccountBridgeError("execution receipt data snapshot hash does not match quote evidence")
    if not isinstance(risk, dict) or risk.get("approved") is not True:
        raise PaperAccountBridgeError("paper account sync requires an approved persisted risk verdict")

    side = decision.get("side")
    symbol = decision.get("instrument")
    quantity = decision.get("quantity")
    if side not in {"BUY", "SELL"} or not isinstance(symbol, str) or type(quantity) is not int or quantity <= 0:
        raise PaperAccountBridgeError("decision must identify a cash-equity side, symbol and positive quantity")
    fills = receipt.get("fills", [])
    status = receipt.get("status")
    if not isinstance(fills, list):
        raise PaperAccountBridgeError("receipt fills must be a list")
    fill_qty = sum((int(fill.get("quantity", 0)) for fill in fills if isinstance(fill, dict)), 0)
    if status == "FILLED" and fill_qty != quantity:
        raise PaperAccountBridgeError("FILLED receipt quantity does not equal the decision quantity")
    if status == "PARTIAL" and not 0 < fill_qty < quantity:
        raise PaperAccountBridgeError("PARTIAL receipt needs a positive quantity below the decision quantity")
    if status not in {"FILLED", "PARTIAL", "MISSED", "REJECTED", "NO_TRADE", "CANCELED"}:
        raise PaperAccountBridgeError("receipt has an unsupported simulated outcome")
    if status == "CANCELED" and fill_qty >= quantity:
        raise PaperAccountBridgeError("CANCELED receipt must leave some requested quantity unfilled")
    if status in {"MISSED", "REJECTED", "NO_TRADE"} and fills:
        raise PaperAccountBridgeError("non-fill receipt cannot carry fill facts")

    if side == "BUY":
        try:
            reserve = Decimal(str(risk["account_cash_reserve_inr"]))
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise PaperAccountBridgeError("approved BUY risk verdict must include account_cash_reserve_inr") from exc
        if not reserve.is_finite() or reserve <= 0:
            raise PaperAccountBridgeError("account cash reserve must be positive and finite")
    else:
        reserve = Decimal(0)

    limit = Decimal(str(decision["limit"])) if decision.get("limit") is not None else None
    try:
        decision_record = next((row for row in DurableJournal(Path(runner_journal_path)).records()
                                if row.get("t") == "DECISION"
                                and row.get("session_id") == config.session_id), None)
        if decision_record is None:
            raise PaperAccountBridgeError("paper intent has no persisted decision timestamp")
        decision_at = datetime.fromisoformat(decision_record["decision_at_utc"])
        paper_books.submit_intent(
            plan_id=paper_book_plan_id, strategy_id=strategy_id, account=account,
            session_at=decision_at, client_id=intent_id, symbol=symbol, side=side,
            quantity=quantity, limit=limit, reserve=reserve,
            family_evidence=config.family_evidence, owner_approval=config.family_owner_approval,
        )
        order = account.oms.order(intent_id)
        if order.state is State.CREATED:
            account.move(intent_id, State.SUBMITTED)
            order = account.oms.order(intent_id)
        if order.state is State.SUBMITTED:
            account.move(intent_id, State.ACCEPTED)
            order = account.oms.order(intent_id)
        if fills:
            fill_day = date.fromisoformat(config.exchange_date)
            for fill in fills:
                if not isinstance(fill, dict):
                    raise PaperAccountBridgeError("receipt fill must be a dictionary")
                evidence = {key: fill.get(key) for key in evidence_fields}
                if evidence != {key: receipt[key] for key in evidence_fields}:
                    raise PaperAccountBridgeError("fill provenance does not match its immutable receipt")
                quote_digest = fill.get("quote_event_sha256")
                if quote_digest not in quote_hashes:
                    raise PaperAccountBridgeError("fill quote is absent from the receipt data snapshot")
                account.record_fill(intent_id, fill_id=str(fill["fill_id"]),
                                    quantity=int(fill["quantity"]), price=Decimal(str(fill["price"])),
                                    filled_on=fill_day, execution_evidence=evidence)
            order = account.oms.order(intent_id)
            if order.remaining and order.state is State.PARTIAL:
                account.move(intent_id, State.CANCEL_PENDING)
                account.move(intent_id, State.CANCELED)
        elif order.state in {State.CREATED, State.SUBMITTED}:
            account.move(intent_id, State.REJECTED)
        elif order.state is State.ACCEPTED:
            # A simulated no-fill ends as a cancellation; the OMS intentionally has
            # no ACCEPTED -> REJECTED transition because rejection precedes acceptance.
            account.move(intent_id, State.CANCEL_PENDING)
            account.move(intent_id, State.CANCELED)
    except (PaperAccountError, PaperBookRegistryError, ValueError, KeyError, InvalidOperation) as exc:
        if isinstance(exc, PaperAccountBridgeError):
            raise
        raise PaperAccountBridgeError(f"cash-equity OMS/account refused receipt: {exc}") from exc

    execution_at = datetime.fromisoformat(str(receipt["simulated_execution_at_utc"]))
    mark = _mark_latest_after_fill(account, symbol, intent_id, events, execution_at)
    order = account.oms.order(intent_id)
    position = account.position(symbol)
    ledger_cash = account.ledger.balance(f"cash:{account.account_id}", account.currency)
    if ledger_cash != account.cash or not account.ledger.trial_balance_ok():
        raise PaperAccountBridgeError("cash OMS and double-entry ledger failed reconciliation")
    try:
        equity = str(account.equity)
        open_basis = sum((account.position(symbol_name).average_cost * account.position(symbol_name).quantity
                          for symbol_name in {record["symbol"] for record in account.journal.records()
                                              if record.get("t") == "INTENT"}), Decimal(0))
        unrealized = str(account.equity - account.cash - open_basis)
    except PaperAccountError:
        equity = None
        unrealized = None
    canonical_events = _persist_canonical_events(
        journal_path=Path(runner_journal_path), config=config, result=result,
        account_id=account.account_id, strategy_id=strategy_id, mark=mark,
    )
    row = {
        "t": "ACCOUNT_RECONCILIATION", "session_id": config.session_id,
        "intent_id": intent_id, "account_id": account.account_id,
        "account_journal_seq": account.journal.seq, "order_state": order.state.value,
        "cash_inr": str(account.cash), "available_cash_inr": str(account.available_cash),
        "fees_inr": str(account.fees), "realized_pnl_inr": str(account.realized_pnl),
        "net_realized_pnl_inr": str(account.net_realized_pnl),
        "unrealized_pnl_inr": unrealized, "equity_inr": equity,
        "position": {"symbol": symbol, "quantity": position.quantity,
                     "average_cost": str(position.average_cost)},
        "mark": mark,
        "fill_ids": sorted(str(fill["fill_id"]) for fill in fills),
        "execution_model_id": receipt["execution_model_id"],
        "execution_model_sha256": receipt["execution_model_sha256"],
        "fee_schedule_sha256": receipt["fee_schedule_sha256"],
        "latency_model_sha256": receipt["latency_model_sha256"],
        "data_snapshot_sha256": receipt["data_snapshot_sha256"],
        "canonical_event_sha256s": sorted(event["event_sha256"] for event in canonical_events),
        "physical_orders": False,
    }
    digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    row["reconciliation_sha256"] = digest
    journal = DurableJournal(Path(runner_journal_path))
    prior = next((record for record in journal.records()
                  if record.get("t") == "ACCOUNT_RECONCILIATION"
                  and record.get("session_id") == config.session_id
                  and record.get("intent_id") == intent_id), None)
    if prior is None:
        journal.append(row)
        return row
    if prior.get("reconciliation_sha256") != digest:
        raise RunnerError("paper account reconciliation id is already bound to different accounting facts")
    return prior


def _require_distinct_authority_journals(runner_journal_path: Path, account_journal_path: Path) -> None:
    """Keep runner outbox evidence separate from the authoritative OMS/accounting journal."""
    if Path(runner_journal_path).resolve() == Path(account_journal_path).resolve():
        raise PaperAccountBridgeError(
            "forward-session evidence journal must be distinct from the PaperAccount authority journal"
        )


def _persist_canonical_events(*, journal_path: Path, config: ForwardRunConfig, result: dict,
                              account_id: str, strategy_id: str, mark: dict | None) -> list[dict]:
    """Mirror the cash-paper lifecycle into the shared immutable cross-asset event contract."""
    decision, receipt = result["decision"], result["execution"]
    symbol = str(decision["instrument"])
    if ":" not in symbol:
        raise PaperAccountBridgeError("canonical cash-paper events require VENUE:SYMBOL identity")
    venue, ticker = symbol.split(":", 1)
    instrument = InstrumentRef(venue, ticker, AssetClass.EQUITY)
    side = EventSide(str(decision["side"]))
    intent_id = str(result["intent_id"])
    rows = DurableJournal(journal_path).records()
    decision_row = next((row for row in rows if row.get("t") == "DECISION"
                         and row.get("session_id") == config.session_id), None)
    if decision_row is None:
        raise PaperAccountBridgeError("canonical event export is missing its durable decision timestamp")
    decided_at = datetime.fromisoformat(decision_row["decision_at_utc"])
    try:
        executed_at = datetime.fromisoformat(str(receipt["simulated_execution_at_utc"]))
    except (KeyError, ValueError) as exc:
        raise PaperAccountBridgeError("canonical order events need a valid simulated execution timestamp") from exc
    if executed_at < decided_at:
        raise PaperAccountBridgeError("simulated execution timestamp precedes its sealed decision")
    receipt_sha = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
    events: list[TradingEvent] = [IntentEvent(
        event_id=f"{intent_id}:intent", order_id=intent_id, account_id=account_id,
        strategy_id=strategy_id, instrument=instrument, side=side,
        quantity=int(decision["quantity"]), occurred_at=decided_at,
        source_sha256=config.strategy_sha256,
    ), OrderEvent(
        event_id=f"{intent_id}:state:ACCEPTED", order_id=intent_id,
        status=OrderStatus.ACCEPTED, occurred_at=decided_at, source_sha256=receipt_sha,
    )]
    final_status = receipt.get("status")
    states: tuple[OrderStatus, ...]
    if final_status == "FILLED":
        states = (OrderStatus.FILLED,)
    elif final_status == "PARTIAL":
        states = (OrderStatus.PARTIAL, OrderStatus.CANCELED)
    elif final_status == "REJECTED":
        states = (OrderStatus.REJECTED,)
    else:
        states = (OrderStatus.CANCEL_PENDING, OrderStatus.CANCELED)
    for status in states:
        events.append(OrderEvent(
            event_id=f"{intent_id}:state:{status.value}", order_id=intent_id,
            status=status, occurred_at=executed_at, source_sha256=receipt_sha,
            reason=str(receipt.get("reason") or "SIMULATED_OUTCOME"),
        ))
    for fill in receipt.get("fills", []):
        events.append(FillEvent(
            event_id=str(fill["fill_id"]), order_id=intent_id, instrument=instrument, side=side,
            quantity=int(fill["quantity"]), price=Decimal(str(fill["price"])),
            occurred_at=datetime.fromisoformat(str(fill["quote_available_utc"])),
            fidelity=str(receipt["fidelity"]), source_sha256=str(fill["quote_event_sha256"]),
        ))
    if mark is not None:
        events.append(MarkEvent(
            event_id=f"{intent_id}:mark", instrument=instrument, price=Decimal(str(mark["price"])),
            occurred_at=datetime.fromisoformat(str(mark["observed_at_utc"])),
            fidelity="SIMULATED_QUOTE", source_sha256=str(mark["source_event_sha256"]),
        ))
    journal = DurableJournal(journal_path)
    prior = {row.get("event_id"): row for row in journal.records()
             if row.get("t") == "CANONICAL_TRADING_EVENT" and row.get("session_id") == config.session_id}
    additions, persisted = [], []
    for event in events:
        payload = canonical_event_payload(event).decode("utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        row = {"t": "CANONICAL_TRADING_EVENT", "session_id": config.session_id,
               "event_id": event.event_id, "event_sha256": digest, "payload": payload}
        previous = prior.get(event.event_id)
        if previous is not None and previous != row:
            raise PaperAccountBridgeError("canonical event id is already bound to different event content")
        if previous is None:
            additions.append(row)
        persisted.append(row)
    journal.append_many(additions)
    return persisted


def _mark_latest_after_fill(account: PaperAccount, symbol: str, intent_id: str,
                            events: list[NormalizedEvent], execution_at: datetime) -> dict | None:
    position = account.position(symbol)
    if position.quantity <= 0:
        return None
    candidates = []
    for event in events:
        if event.kind != "QUOTE" or event.instrument != symbol or event.available_utc < execution_at:
            continue
        payload = dict(event.payload)
        try:
            bid, ask = Decimal(payload["bid_price"]), Decimal(payload["ask_price"])
            bid_qty, ask_qty = int(payload["bid_qty"]), int(payload["ask_qty"])
        except (KeyError, ValueError, InvalidOperation):
            continue
        if bid.is_finite() and ask.is_finite() and 0 < bid <= ask and bid_qty > 0 and ask_qty > 0:
            candidates.append((event, bid))
    if not candidates:
        return None
    event, bid = max(candidates, key=lambda pair: (pair[0].available_utc, pair[0].content_sha256()))
    mark_id = hashlib.sha256(f"{intent_id}:{symbol}:{event.content_sha256()}".encode()).hexdigest()
    account.record_mark(symbol, mark_id=mark_id, price=bid, observed_at=event.available_utc)
    return {"price": str(bid), "source_event_sha256": event.content_sha256(),
            "observed_at_utc": event.available_utc.isoformat(), "marking_policy": "LONG_LIQUIDATION_BID"}
