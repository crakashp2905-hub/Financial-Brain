"""Idempotently post forward-session simulation receipts to the cash-equity paper account."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import Callable, Iterable

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError


class PaperAccountBridgeError(ValueError):
    """The durable paper receipt cannot be reconciled to the cash-equity account."""


class ForwardPaperSessionService:
    """Run explicit preflight, durable forward session, and idempotent account reconciliation."""

    def __init__(self, runner: ForwardSessionRunner, account: PaperAccount) -> None:
        self.runner = runner
        self.account = account

    def run(self, config: ForwardRunConfig, events: Iterable[NormalizedEvent], *,
            preflight_fn: Callable[[], dict], decision_fn: Callable[[list[NormalizedEvent]], dict],
            risk_fn: Callable[[dict], dict], execution_fn: Callable[[dict, str, list[NormalizedEvent]], dict],
            decision_at: datetime | None = None,
            reconcile_execution_fn: Callable[[str], dict] | None = None) -> dict:
        """Persist one preflight, run the session, then reconcile its receipt to PaperAccount."""
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
        captured_events = list(events)
        result = self.runner.run(
            config, captured_events, decision_at=decision_at,
            decision_fn=decision_fn, risk_fn=risk_fn, execution_fn=execution_fn,
            reconcile_execution_fn=reconcile_execution_fn,
        )
        if result.get("status") == "PAPER_COMPLETE":
            evidence = reconcile_paper_account(
                runner_journal_path=self.runner.journal_path, config=config, result=result,
                account=self.account, events=captured_events,
            )
            return {**result, "account_reconciliation": evidence}
        return result


def reconcile_paper_account(*, runner_journal_path: Path, config: ForwardRunConfig,
                            result: dict, account: PaperAccount,
                            events: list[NormalizedEvent]) -> dict:
    """Post a closed runner receipt to PaperAccount, then persist cross-journal reconciliation.

    Stable intent/fill IDs make replays safe after a crash between an account event and its
    reconciliation marker. BUY decisions must carry an explicit cash reserve in the risk
    verdict; the bridge never invents capital allocation. A fee schedule is mandatory.
    """
    if account.fee_schedule is None:
        raise PaperAccountBridgeError("paper account needs an effective-dated fee schedule")
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
    snapshot_digest = hashlib.sha256(json.dumps(quote_hashes, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode("utf-8")).hexdigest()
    if not isinstance(quote_hashes, list) or snapshot_digest != receipt["data_snapshot_sha256"]:
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
    if status not in {"FILLED", "PARTIAL", "MISSED", "REJECTED", "NO_TRADE"}:
        raise PaperAccountBridgeError("receipt has an unsupported simulated outcome")
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
        account.submit_intent(client_id=intent_id, symbol=symbol, side=side, quantity=quantity,
                              limit=limit, reserve=reserve)
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
                account.record_fill(intent_id, fill_id=str(fill["fill_id"]),
                                    quantity=int(fill["quantity"]), price=Decimal(str(fill["price"])),
                                    filled_on=fill_day, execution_evidence=evidence)
            order = account.oms.order(intent_id)
            if order.remaining and order.state is State.PARTIAL:
                account.move(intent_id, State.CANCEL_PENDING)
                account.move(intent_id, State.CANCELED)
        elif order.state in {State.CREATED, State.SUBMITTED, State.ACCEPTED}:
            account.move(intent_id, State.REJECTED)
    except (PaperAccountError, ValueError, KeyError, InvalidOperation) as exc:
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
