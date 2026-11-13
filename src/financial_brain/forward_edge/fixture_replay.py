"""Run the offline forward-edge fixture pack through the production session coordinator.

Fixtures are synthetic engineering evidence only; they are not market observations.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from financial_brain.forward_edge import execution as X
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode, ModeError, resolve_mode
from financial_brain.forward_edge.session_runner import (
    ExecutionOutcomeUnknown, ForwardRunConfig, ForwardSessionRunner,
)


def _sha(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=str, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def fixture_events(fx: dict) -> list[NormalizedEvent]:
    """Convert fixture quote/trade rows into the same availability-timestamped event schema."""
    decision = datetime.fromisoformat(fx["decision_utc"]).astimezone(timezone.utc)
    instrument = str(fx["instrument"])
    events = []
    for kind, rows in (("QUOTE", fx.get("quotes", [])), ("TICK", fx.get("trades", []))):
        for row in rows:
            available = decision + timedelta(milliseconds=int(row["offset_ms"]))
            payload = ({"bid_price": row["bid"], "ask_price": row["ask"],
                        "bid_qty": row["bid_qty"], "ask_qty": row["ask_qty"]}
                       if kind == "QUOTE" else {"price": row["price"], "quantity": row["qty"]})
            events.append(NormalizedEvent(
                kind=kind,
                instrument=instrument,
                event_utc=available - timedelta(milliseconds=2),
                received_utc=available - timedelta(milliseconds=1),
                available_utc=available,
                payload=tuple(sorted((key, str(value)) for key, value in payload.items())),
            ))
    return sorted(events, key=lambda event: (event.available_utc, event.content_sha256()))


def _receipt(decision: dict, intent_id: str, events: list[NormalizedEvent], fx: dict) -> dict:
    decision_at = datetime.fromisoformat(decision["decision_at_utc"]).astimezone(timezone.utc)
    instrument = str(decision["instrument"])
    intent = X.OrderIntent(instrument, str(decision["side"]), int(decision["quantity"]),
                           decision_at, Decimal(str(fx["tick"])),
                           Decimal(str(fx["limit"])) if fx.get("limit") else None)
    quote_events = [event for event in events if event.kind == "QUOTE" and event.instrument == instrument]
    quotes = [X.Quote(event.available_utc, Decimal(dict(event.payload)["bid_price"]),
                      Decimal(dict(event.payload)["ask_price"]), int(dict(event.payload)["bid_qty"]),
                      int(dict(event.payload)["ask_qty"])) for event in quote_events]
    latency = timedelta(milliseconds=int(fx.get("latency_ms", 200)))
    first = X.earliest_quote(quotes, decision_at, latency)
    max_wait = timedelta(milliseconds=int(fx.get("max_wait_ms", 5000)))
    feed = fx.get("feed", {"ok": True, "reasons": []})
    used_events: list[NormalizedEvent] = []
    if first is not None and first.available_utc - decision_at > max_wait:
        result = X.ExecResult("MISSED", 0, None, "FIRST_QUOTE_AFTER_MAX_WAIT")
    elif fx.get("passive"):
        limit = intent.limit
        if limit is None:
            raise ValueError("passive fixture intent needs a limit")
        trades = [(event.available_utc, Decimal(dict(event.payload)["price"]),
                   int(dict(event.payload)["quantity"]))
                  for event in events if event.kind == "TICK" and event.instrument == instrument]
        result = X.passive_limit(intent, trades, latency)
        if fx.get("adverse_bps"):
            result = X.apply_adverse_selection(result, intent.side, Decimal(str(fx["adverse_bps"])))
        remaining = result.filled_qty
        for event in sorted((event for event in events if event.kind == "TICK" and event.instrument == instrument),
                            key=lambda item: (item.available_utc, item.content_sha256())):
            payload = dict(event.payload)
            price, available_qty = Decimal(payload["price"]), int(payload["quantity"])
            through = price < limit if intent.side == "BUY" else price > limit
            if event.available_utc >= decision_at + latency and through and remaining > 0:
                used_events.append(event)
                remaining -= min(remaining, available_qty)
    elif not feed.get("ok", True):
        result = X.execute_or_no_trade(False, list(feed.get("reasons", [])), intent, quotes, latency)
    else:
        band = tuple(Decimal(str(value)) for value in fx["price_band"]) if fx.get("price_band") else None
        result = X.marketable_limit(intent, quotes, latency, band)  # type: ignore[arg-type]
    if fx.get("ack_lost"):
        raise ExecutionOutcomeUnknown("fixture acknowledgement lost after simulated send")
    if result.filled_qty and result.avg_price is not None:
        if not fx.get("passive") and first is not None:
            matched_event = next((item for item in quote_events
                                  if item.available_utc == first.available_utc), None)
            if matched_event is not None:
                used_events = [matched_event]
        execution_at = max((event.available_utc for event in used_events), default=decision_at + latency)
    else:
        execution_at = first.available_utc if first is not None else decision_at + latency
    source_hash = _sha(sorted(event.content_sha256() for event in used_events))
    def input_ref(event: NormalizedEvent) -> dict:
        return {"input_id": f"event:{event.content_sha256()}",
                "kind": event.kind,
                "event_sha256": event.content_sha256(),
                "available_utc": event.available_utc.astimezone(timezone.utc).isoformat()}
    quote_inputs = [input_ref(event) for event in quote_events]
    used_input_ids = [input_ref(event)["input_id"] for event in used_events]
    receipt = {
        "receipt_id": f"fixture-sim-{intent_id}", "simulator": "fixture-replay-v1",
        "fidelity": result.fidelity, "status": result.status, "reason": result.reason,
        "instrument": instrument, "side": intent.side, "requested_quantity": intent.quantity,
        "simulated_execution_at_utc": execution_at.isoformat(),
        "quote_event_sha256s": sorted(event.content_sha256() for event in quote_events),
        "quote_inputs": sorted(quote_inputs, key=lambda row: (row["available_utc"], row["input_id"])),
        "used_input_ids": used_input_ids,
        "source_event_sha256s": sorted(event.content_sha256() for event in events),
        "fills": [],
    }
    if result.filled_qty and result.avg_price is not None:
        fill_id = hashlib.sha256(f"{intent_id}:{source_hash}".encode()).hexdigest()
        receipt["fills"] = [{"fill_id": fill_id, "quantity": result.filled_qty,
                             "price": str(result.avg_price),
                             "source_events_sha256": source_hash,
                             "source_event_ids": used_input_ids,
                             "quote_input_ids": [row["input_id"] for row in quote_inputs
                                                 if row["event_sha256"] in {
                                                     event.content_sha256() for event in used_events
                                                 }]}]
    return receipt


def run_fixture_on_forward_runner(fx: dict, journal_path: Path | None = None,
                                  *, mode: Mode = Mode.LIVE_PAPER, broker: str = "NONE") -> dict:
    """Replay one synthetic session through ``ForwardSessionRunner`` and return normalized outcome facts."""
    decision_at = datetime.fromisoformat(fx["decision_utc"]).astimezone(timezone.utc)
    session_mode = resolve_mode(mode, now=decision_at)
    if session_mode.mode is not Mode.LIVE_PAPER or broker != "NONE":
        raise ModeError("fixture replay emits fills only in LIVE_PAPER mode with no named broker")
    constraints = _sha({key: value for key, value in fx.items()
                        if key not in {"session_id", "decision_utc"}})
    config = ForwardRunConfig(
        session_id=str(fx["session_id"]), exchange_date=decision_at.date().isoformat(),
        cohort_id="synthetic-fixture", strategy_sha256=str(fx["strategy_sha256"]),
        risk_policy_sha256=str(fx["risk_sha256"]), constraints_sha256=constraints,
        ledger_id="fixture-ledger", benchmark_id=str(fx["benchmark_id"]),
        instruments=(str(fx["instrument"]),), capital_inr="1000000", mode=session_mode.mode,
    )
    temp_dir = None
    if journal_path is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="financial-brain-fixture-")
        journal_path = Path(temp_dir.name) / "session.jsonl"
    runner = ForwardSessionRunner(journal_path)
    checks = fx.get("preflight", {"calendar": True, "credentials": True, "disk": True, "feeds": True,
                                   "data_contracts": True, "risk": True, "system_health": True,
                                   "broker_disabled": True})
    approved = all(checks.get(key) is True for key in checks) and all(
        checks.get(key) is True for key in ("calendar", "credentials", "disk", "feeds",
                                            "data_contracts", "risk", "system_health", "broker_disabled"))
    runner.record_preflight(config, {"approved": approved,
                                     "reason": "PREFLIGHT_BLOCKED" if not approved else "PASS",
                                     "checks": checks})
    signal = {"side": str(fx["side"]), "instrument": str(fx["instrument"]),
              "quantity": int(fx["quantity"]), "limit": fx.get("limit"),
              "expected_net_alpha": fx.get("expected_net_alpha"),
              "forecast_horizon_sessions": fx.get("forecast_horizon_sessions"),
              "forecast_costs_included": fx.get("forecast_costs_included") is True,
              "net_alpha_forecast": fx.get("net_alpha_forecast")}
    try:
        result = runner.run(
            config, fixture_events(fx), decision_at=decision_at,
            decision_fn=lambda _visible: signal,
            risk_fn=lambda _decision: {"approved": True},
            execution_fn=lambda decision, intent_id, events: _receipt(decision, intent_id, events, fx),
        )
        receipt = result.get("execution")
        if not approved:
            outcome, reason = "NO_TRADE", "PREFLIGHT_BLOCKED"
        elif result["status"] == "REJECTED":
            outcome, reason = "REJECTED", str(result.get("reason", "PREFLIGHT_REJECTED"))
        elif result["status"] == "NO_TRADE":
            outcome, reason = "NO_TRADE", str(result.get("reason", "NO_SIGNAL"))
        elif receipt:
            execution_status = receipt["status"]
            if execution_status in {"FILLED", "PARTIAL"}:
                filled = receipt["fills"][0]
                qty, price = int(filled["quantity"]), Decimal(str(filled["price"]))
                entry = price * qty
                close = Decimal(str(fx["close_price"])) * qty
                sign = Decimal(1) if fx["side"] == "BUY" else Decimal(-1)
                fee_rate = Decimal(str(fx.get("fee_bps", "3"))) / Decimal(10_000)
                net = close * sign - entry * sign - (entry + close) * fee_rate
                outcome = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
                reason = f"FILLED_{execution_status}"
            elif execution_status == "REJECTED":
                outcome, reason = "REJECTED", str(receipt["reason"])
            elif execution_status == "NO_TRADE":
                outcome, reason = "NO_TRADE", str(receipt["reason"])
            elif receipt["reason"] == "FIRST_QUOTE_AFTER_MAX_WAIT":
                outcome, reason = "LATE_QUOTE", str(receipt["reason"])
            else:
                outcome, reason = "MISSED_FILL", str(receipt["reason"])
        else:
            outcome, reason = "NO_TRADE", str(result.get("reason", "NO_SIGNAL"))
        return {"outcome": outcome, "reason": reason, "runner_result": result,
                "physical_orders": False, "mode": config.mode.value}
    except ExecutionOutcomeUnknown:
        return {"outcome": "UNKNOWN_ORDER", "reason": "ACK_LOST_NO_RESEND",
                "runner_result": None, "physical_orders": False, "mode": config.mode.value}
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
