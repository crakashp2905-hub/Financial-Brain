"""Record deterministic fixture decisions through the canonical forward-session journal.

This adapter records paper execution through the canonical runner. All evidence remains
explicitly local/synthetic and proves plumbing, not market performance or profitability.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

from financial_brain.costs.india import CostModel, Segment
from financial_brain.kernel.events import UnknownOutcome
from financial_brain.forward_edge.cost_schedule import CostSchedule
from financial_brain.forward_edge.instrument_rules import fixture_rule_book
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_runner import ForwardSessionRunner
from financial_brain.forward_edge.session_state import ForwardRunConfig, ForwardSessionStore, SessionState

IST = ZoneInfo("Asia/Kolkata")
FIXTURE_PROVENANCE: dict[str, str | None] = {
    "source_commit": None,
    "source_branch": None,
    "source_tree_state": "SYNTHETIC_FIXTURE",
    "branch_ancestry_sha256": None,
    "executable_entry_point": "ForwardSessionRunner:synthetic-fixture",
}

def _fixture_cost_schedule(day, fee_bps: str) -> tuple[dict[str, Any], str]:
    fee_rate = float(Decimal(fee_bps) / Decimal(10_000))
    model = CostModel(
        stt_delivery=0, stt_intraday_sell=0, stt_futures_sell=0, stt_options_sell=0,
        exch_txn_equity=0, exch_txn_futures=0, exch_txn_options=0, sebi_turnover=0,
        stamp_buy_delivery=0, stamp_buy_intraday=0, gst_rate=0, dp_charge_flat=0,
        brokerage_rate=fee_rate, brokerage_cap_flat=1_000_000, brokerage_delivery_flat=0,
        impact={"mega": 0, "large": 0, "mid": 0, "small": 0, "micro": 0},
    )
    source = {"fixture_fee_bps_per_side": fee_bps, "scope": "synthetic golden compatibility only"}
    source_sha = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"))
                                .encode("utf-8")).hexdigest()
    schedule = CostSchedule("fixture-bps", "v1", day, None, Segment.INTRADAY, model,
                            "synthetic fixture fee assumption", source_sha)
    body = {"schedule_id": schedule.schedule_id, "version": schedule.version,
            "effective_from": schedule.effective_from.isoformat(), "effective_to": None,
            "segment": schedule.segment.value, "model": model.as_dict(),
            "source_ref": schedule.source_ref, "source_sha256": schedule.source_sha256}
    return body, schedule.digest()


def _quote_event(event_id: str, instrument: str, price_row: dict[str, Any], at: datetime) -> MarketEvent:
    bid, ask = float(price_row["bid"]), float(price_row["ask"])
    bid_quantity, ask_quantity = int(price_row["bid_qty"]), int(price_row["ask_qty"])
    payload = {"bid": bid, "ask": ask, "bid_quantity": bid_quantity, "ask_quantity": ask_quantity,
               "volume": max(1, bid_quantity + ask_quantity), "session_phase": "CONTINUOUS",
               "depth": {"buy": [[bid, bid_quantity]], "sell": [[ask, ask_quantity]]}}
    evidence_id = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode("utf-8")).hexdigest()
    return MarketEvent(event_id, EventKind.QUOTE, "NSE", instrument, at, at, at, payload,
                       "SYNTHETIC_FIXTURE", evidence_id=evidence_id)


def _manifest(*, runner: ForwardSessionRunner, session_id: str, decision_id: str,
              decision_at: datetime, source: MarketEvent, instrument: str, quantity: int,
              strategy_sha256: str, risk_sha256: str, benchmark: str,
              cost_schedule_sha256: str, latency_ms: int, selected: bool) -> dict[str, Any]:
    source_sha = runner._con.execute(
        "SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
        [session_id, source.event_id]).fetchone()[0]
    selected_row = [{"lineage": instrument, "symbol": instrument, "weight_fraction": "0.01",
                     "quantity": quantity}] if selected else []
    return {
        "schema": "fb.decision.v1", "day": decision_at.astimezone(IST).date().isoformat(),
        "decision_id": decision_id, "author": "offline-fixture-adapter",
        "strategy_sha256": strategy_sha256, "model_sha256": risk_sha256,
        "data_sha256": source_sha,
        "universe_sha256": hashlib.sha256(instrument.encode()).hexdigest(),
        "picks_sha256": hashlib.sha256((instrument if selected else "cash").encode()).hexdigest(),
        "calendar_sha256": hashlib.sha256(b"fixture-calendar-not-venue-evidence").hexdigest(),
        "cost_model_sha256": cost_schedule_sha256, "risk_limits_sha256": risk_sha256,
        "decision_at_utc": decision_at.isoformat(),
        "entry_earliest_at_utc": (decision_at + timedelta(milliseconds=latency_ms)).isoformat(),
        "exit_scheduled_at_utc": (decision_at + timedelta(hours=8)).isoformat(),
        "data_watermark_at_utc": source.available_at.isoformat(),
        "exchange_session_id": f"NSE-{decision_at.astimezone(IST).date().isoformat()}",
        "holding_sessions": 1, "benchmark_id": benchmark, "capital_inr": "1000000",
        "predicted_return_fraction": "0.01", "predicted_cost_fraction": "0.001",
        "forecast": {"p10_fraction": "-0.01", "p50_fraction": "0.01", "p90_fraction": "0.02",
                     "probability_exceeds_cost": "0.5"},
        "selections": selected_row, "exclusions": [],
        "sources": [{"source_id": source.event_id, "sha256": source_sha,
                     "published_at_utc": source.event_time.isoformat(),
                     "received_at_utc": source.received_at.isoformat(),
                     "available_at_utc": source.available_at.isoformat()}],
        "no_trade_reasons": [] if selected else ["FIXTURE_NO_TRADE"],
    }


def record_fixture_decision(fx: dict[str, Any], legacy_result: dict[str, Any], *,
                            state_dir: Path | None = None) -> dict[str, Any]:
    """Journal a fixture's source observation and decision without upgrading its evidence tier.

    `state_dir` is intended for tests and explicit offline investigations. By default, the
    SQLite files are temporary; the result returns their canonical records digest and counts.
    """
    decision_at = datetime.fromisoformat(fx["decision_utc"]).astimezone(timezone.utc)
    session_date = decision_at.astimezone(IST).date()
    instrument = f"NSE:{fx['instrument']}"
    session_id = str(fx["session_id"])
    identity = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    decision_id = f"fixture-{identity}-decision"
    if state_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="fb-forward-fixture-")
        directory = Path(temporary.name)
    else:
        temporary = None
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
    # Pin provenance only for this synthetic adapter so golden fixture hashes do not
    # depend on the developer checkout or GitHub's synthetic PR merge commit.
    with patch("financial_brain.forward_edge.session_runner.repository_provenance",
               return_value=FIXTURE_PROVENANCE):
        runner = ForwardSessionRunner(ForwardSessionStore(directory / "state.sqlite"),
                                      directory / "journal.sqlite")
    try:
        cost_schedule_data, cost_schedule_sha256 = _fixture_cost_schedule(
            session_date, str(fx.get("fee_bps", "3")))
        quantity = int(fx["quantity"])
        config = ForwardRunConfig(
            session_date, "fixture-v1", str(fx["strategy_sha256"]), str(fx["benchmark_id"]),
            (instrument,), Decimal("1000000"),
            {"max_positions": 1, "max_quote_age_ms": 5_000, "session_phase": "CONTINUOUS",
             "allow_synthetic_fixture_events": True,
             "reservation_policy": {
                 "version": "local-fixture-reservation-v1",
                 "frozen_at": "1900-01-01T00:00:00+00:00",
                 "source_sha256": hashlib.sha256(b"local-fixture-reservation-v1").hexdigest(),
                 "limits": {"CASH": "1000000000000",
                            "PAPER_COST_BUFFER": "1000000000",
                            f"INSTRUMENT_QUANTITY:{instrument}": "1000000000",
                            f"SELLABLE_INVENTORY:{instrument}": "1000000000"},
             },
             "liquidity_bucket": "large", "cost_schedule": cost_schedule_data,
             "instrument_rules": fixture_rule_book(instrument, tick_size=str(fx["tick"]),
                                                     freeze_quantity=max(1000, quantity))},
            "fixture-ledger-v1",
        )
        fixture_checks = fx.get("preflight", {})
        checks = {name: fixture_checks.get(name, True) is True for name in (
            "calendar", "credentials", "disk", "feeds", "data_contracts", "risk", "system_health",
            "broker_disabled")}
        runner_state = runner.begin(session_id, config, now=decision_at - timedelta(seconds=1),
                                    preflight=checks)
        if runner_state is not SessionState.OPEN:
            return {"session_id": session_id, "state": runner_state.value, "event_count": 0,
                    "decision_count": 0, "paper_intent_count": 0, "receipt_count": 0,
                    "report_status": "REJECTED", "records_sha256": None,
                    "physical_orders": False, "evidence_tier": "LOCAL_FIXTURE"}

        first_quote = fx["quotes"][0]
        bid = float(first_quote["bid"])
        ask = float(first_quote["ask"])
        event_time = decision_at - timedelta(milliseconds=3)
        received_at = decision_at - timedelta(milliseconds=2)
        available_at = decision_at - timedelta(milliseconds=1)
        payload = {"bid": bid, "ask": ask, "bid_quantity": int(first_quote["bid_qty"]),
                   "ask_quantity": int(first_quote["ask_qty"]), "volume": 1000,
                   "session_phase": "CONTINUOUS",
                   "depth": {"buy": [[bid, int(first_quote["bid_qty"])]],
                             "sell": [[ask, int(first_quote["ask_qty"])] ]}}
        evidence_id = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode("utf-8")).hexdigest()
        event = MarketEvent(f"{session_id}:source", EventKind.QUOTE, "NSE", instrument, event_time,
                            received_at, available_at, payload, "SYNTHETIC_FIXTURE", evidence_id=evidence_id)
        runner.ingest(session_id, config, [event], ingest_metadata={
            event.event_id: (decision_at, "FIXTURE_CLOCK")})

        outcome = str(legacy_result["outcome"])
        no_trade = outcome == "NO_TRADE"
        runner.decide(session_id=session_id, strategy_id="fixture-legacy-v1", decision_id=decision_id,
                      outcome="NO_TRADE" if no_trade else "TRADE", decided_at=decision_at,
                      input_event_ids=(event.event_id,), reason_codes=("FIXTURE_NO_TRADE",) if no_trade else (),
                      payload={"legacy_outcome": outcome, "legacy_signature": legacy_result["signature"]})
        is_trade = not no_trade
        manifest = _manifest(
            runner=runner, session_id=session_id, decision_id=decision_id, decision_at=decision_at,
            source=event, instrument=instrument, quantity=quantity,
            strategy_sha256=str(fx["strategy_sha256"]), risk_sha256=str(fx["risk_sha256"]),
            benchmark=str(fx["benchmark_id"]), cost_schedule_sha256=cost_schedule_sha256,
            latency_ms=int(fx.get("latency_ms", 200)), selected=is_trade)
        runner.record_decision_manifest(session_id=session_id, decision_id=decision_id, manifest=manifest)
        if is_trade:
            runner.record_risk_verdict(
                session_id=session_id, decision_id=decision_id, verdict_id=f"{decision_id}:risk",
                allowed=True, policy_id="fixture-risk-v1", policy_sha256=str(fx["risk_sha256"]),
                reason_codes=(),
                assessed_at=decision_at,
            )
        order_ids: list[str] = []
        entry_receipt: dict[str, Any] | None = None
        exit_receipt: dict[str, Any] | None = None
        fill_event: MarketEvent | None = None
        unknown_order = False
        latency_ms = int(fx.get("latency_ms", 200))
        if is_trade:
            quote_rows = sorted(fx["quotes"], key=lambda row: int(row["offset_ms"]))
            eligible_rows = [row for row in quote_rows if int(row["offset_ms"]) >= latency_ms]
            if not eligible_rows:
                raise ValueError("fixture has no quote available after simulated order latency")
            execution_quotes = [
                _quote_event(f"{session_id}:execution-quote-{index}", instrument, row,
                             decision_at + timedelta(milliseconds=int(row["offset_ms"])))
                for index, row in enumerate(quote_rows)
            ]
            passive_trade_events = []
            for index, trade in enumerate(fx.get("trades", [])):
                tick_at = decision_at + timedelta(milliseconds=int(trade["offset_ms"]))
                evidence = hashlib.sha256(json.dumps(trade, sort_keys=True, separators=(",", ":"))
                                          .encode("utf-8")).hexdigest()
                passive_trade_events.append(MarketEvent(
                    f"{session_id}:trade-print-{index}", EventKind.TICK, "NSE", instrument,
                    tick_at, tick_at, tick_at,
                    {"price": float(trade["price"]), "quantity": int(trade["qty"])},
                    "SYNTHETIC_FIXTURE", evidence_id=evidence))
            execution_events = [*execution_quotes, *passive_trade_events]
            runner.ingest(session_id, config, execution_events, ingest_metadata={
                row.event_id: (row.available_at + timedelta(milliseconds=1), "FIXTURE_CLOCK")
                for row in execution_events})
            fill_event = next(
                quote_event for quote_event in execution_quotes
                if quote_event.available_at == decision_at + timedelta(
                    milliseconds=int(eligible_rows[0]["offset_ms"]))
            )
            eligible_prints = tuple(
                row.event_id for row in passive_trade_events
                if row.available_at >= decision_at + timedelta(milliseconds=latency_ms)
            )
            simulated_at = max((fill_event.available_at, *(row.available_at for row in passive_trade_events
                                                           if row.event_id in eligible_prints)))
            first_row = eligible_rows[0]
            side = str(fx["side"])
            tick_size = Decimal(str(fx["tick"]))
            if fx.get("limit") is not None:
                limit = Decimal(str(fx["limit"]))
            elif side == "BUY":
                limit = Decimal(str(first_row["ask"])) * 2
            else:
                limit = Decimal(str(first_row["bid"])) / 2
            order_id = f"{session_id}:entry"
            order_ids.append(order_id)
            execute_args: dict[str, Any] = {
                "session_id": session_id, "order_id": order_id, "decision_id": decision_id,
                "event_id": fill_event.event_id, "side": side, "quantity": quantity,
                "limit_price": limit, "tick_size": tick_size, "freeze_limit": max(1000, quantity),
                "slice_size": max(1000, quantity),
                "adverse_slippage_bps": Decimal(str(fx.get("adverse_bps", "0"))) if fx.get("passive")
                else Decimal(0), "latency_ms": latency_ms,
                "simulated_at": simulated_at,
                "max_wait_ms": int(fx.get("max_wait_ms", 5000)),
                "price_band": tuple(Decimal(str(value)) for value in fx["price_band"])
                if fx.get("price_band") else None,
                "passive_trade_event_ids": eligible_prints if fx.get("passive") else (),
            }
            if fx.get("ack_lost"):
                with patch.object(runner, "_persist_paper_receipt",
                                 side_effect=OSError("fixture simulates lost receipt acknowledgement")):
                    try:
                        runner.execute_paper_intent(**execute_args)
                    except UnknownOutcome:
                        pass
                state_row = runner._con.execute("SELECT state FROM outbox WHERE key=?",
                                                [f"paper:{session_id}:{order_id}"]).fetchone()
                unknown_order = state_row is not None and state_row[0] == "UNKNOWN"
                if not unknown_order:
                    raise RuntimeError("ack-loss fixture did not leave its order in UNKNOWN state")
            else:
                entry_receipt = runner.execute_paper_intent(**execute_args)

            if entry_receipt is not None and int(entry_receipt["filled_quantity"]) > 0:
                exit_decision_at = simulated_at + timedelta(milliseconds=1)
                exit_decision_id = f"fixture-{identity}-exit"
                runner.decide(session_id=session_id, strategy_id="fixture-exit-v1",
                              decision_id=exit_decision_id, outcome="TRADE", decided_at=exit_decision_at,
                              input_event_ids=(fill_event.event_id,),
                              payload={"role": "fixture-close", "legacy_outcome": outcome})
                exit_quantity = int(entry_receipt["filled_quantity"])
                runner.record_decision_manifest(
                    session_id=session_id, decision_id=exit_decision_id,
                    manifest=_manifest(
                        runner=runner, session_id=session_id, decision_id=exit_decision_id,
                        decision_at=exit_decision_at, source=fill_event, instrument=instrument,
                        quantity=exit_quantity, strategy_sha256=str(fx["strategy_sha256"]),
                        risk_sha256=str(fx["risk_sha256"]), benchmark=str(fx["benchmark_id"]),
                        cost_schedule_sha256=cost_schedule_sha256, latency_ms=latency_ms, selected=True))
                runner.record_risk_verdict(
                    session_id=session_id, decision_id=exit_decision_id,
                    verdict_id=f"{exit_decision_id}:risk", allowed=True, policy_id="fixture-risk-v1",
                    policy_sha256=str(fx["risk_sha256"]), assessed_at=exit_decision_at)
                close_price = Decimal(str(fx["close_price"]))
                exit_at = exit_decision_at + timedelta(milliseconds=latency_ms)
                exit_row = {"bid": str(close_price), "ask": str(close_price + tick_size),
                            "bid_qty": exit_quantity, "ask_qty": exit_quantity}
                exit_event = _quote_event(f"{session_id}:exit-quote", instrument, exit_row, exit_at)
                runner.ingest(session_id, config, [exit_event], ingest_metadata={
                    exit_event.event_id: (exit_at + timedelta(milliseconds=1), "FIXTURE_CLOCK")})
                exit_order_id = f"{session_id}:exit"
                order_ids.append(exit_order_id)
                sell_limit = max(tick_size, close_price / 2)
                exit_receipt = runner.execute_paper_intent(
                    session_id=session_id, order_id=exit_order_id, decision_id=exit_decision_id,
                    event_id=exit_event.event_id, side="SELL", quantity=exit_quantity,
                    limit_price=sell_limit, tick_size=tick_size, freeze_limit=max(1000, exit_quantity),
                    slice_size=max(1000, exit_quantity), adverse_slippage_bps=Decimal(0),
                    latency_ms=latency_ms, simulated_at=exit_at, max_wait_ms=int(fx.get("max_wait_ms", 5000)))

        records_before_close = runner.records(session_id)
        accounting_before_close = records_before_close["accounting"]
        complete = (not unknown_order and not accounting_before_close["unresolved_order_ids"]
                    and not accounting_before_close["errors"]
                    and not accounting_before_close["positions"])
        close_at = max(decision_at + timedelta(seconds=1),
                       (exit_receipt and datetime.fromisoformat(exit_receipt["simulated_at"])) or
                       (entry_receipt and datetime.fromisoformat(entry_receipt["simulated_at"])) or decision_at)
        report = runner.close_session(
            session_id, complete=complete, now=close_at,
            reason=None if complete else "fixture has an UNKNOWN order or unreconciled open paper position",
        )
        records = runner.records(session_id)
        accounting = records["accounting"]
        if no_trade:
            runner_outcome = "NO_TRADE"
        elif unknown_order:
            runner_outcome = "UNKNOWN_ORDER"
        elif entry_receipt is not None and entry_receipt["status"] == "REJECTED":
            runner_outcome = "REJECTED"
        elif entry_receipt is not None and entry_receipt["status"] == "MISSED":
            runner_outcome = ("LATE_QUOTE" if entry_receipt.get("reject_reason") == "FIRST_QUOTE_AFTER_MAX_WAIT"
                              else "MISSED_FILL")
        elif exit_receipt is not None and accounting["realized_net_pnl_inr"] is not None:
            net = Decimal(accounting["realized_net_pnl_inr"])
            runner_outcome = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
        else:
            runner_outcome = "INCOMPLETE"
        return {"session_id": session_id, "state": runner.state_store.state(session_id).value,
                "event_count": len(records["events"]), "decision_count": len(records["decisions"]),
                "paper_intent_count": len(records["intents"]),
                "receipt_count": len(records["execution_receipts"]), "report_status": report["status"],
                "records_sha256": report["records_sha256"], "physical_orders": False,
                "evidence_tier": "LOCAL_FIXTURE", "economic_outcome": runner_outcome,
                "realized_net_pnl_inr": accounting["realized_net_pnl_inr"],
                "ledger_balanced": accounting["ledger_balanced"],
                "cash_reconciled": accounting["cash_reconciled"],
                "net_pnl_eligible": accounting["net_pnl_eligible"],
                "costs_assessed": accounting["charges_assessed"],
                "order_ids": order_ids,
                "unknown_order_ids": accounting["unresolved_order_ids"]}
    finally:
        runner.close()
        runner.state_store.close()
        if temporary is not None:
            temporary.cleanup()
