"""Record deterministic fixture decisions through the canonical forward-session journal.

This adapter intentionally stops before paper intents and fills. Until the legacy execution
outcomes are represented by runner receipts, economic fixture results remain marked incomplete.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.instrument_rules import fixture_rule_book
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_runner import ForwardSessionRunner
from financial_brain.forward_edge.session_state import ForwardRunConfig, ForwardSessionStore, SessionState

IST = ZoneInfo("Asia/Kolkata")
_SHA = "0" * 64


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
    runner = ForwardSessionRunner(ForwardSessionStore(directory / "state.sqlite"), directory / "journal.sqlite")
    try:
        config = ForwardRunConfig(
            session_date, "fixture-v1", str(fx["strategy_sha256"]), str(fx["benchmark_id"]),
            (instrument,), Decimal("1000000"),
            {"max_positions": 1, "max_quote_age_ms": 5_000, "session_phase": "CONTINUOUS",
             "instrument_rules": fixture_rule_book(instrument, tick_size=str(fx["tick"]))},
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
                   "ask_quantity": int(first_quote["ask_qty"]), "session_phase": "CONTINUOUS"}
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
        event_sha = runner._con.execute(
            "SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
            [session_id, event.event_id]).fetchone()[0]
        is_trade = not no_trade
        manifest = {
            "schema": "fb.decision.v1", "day": session_date.isoformat(), "decision_id": decision_id,
            "author": "offline-fixture-adapter", "strategy_sha256": str(fx["strategy_sha256"]),
            "model_sha256": str(fx["risk_sha256"]), "data_sha256": event_sha,
            "universe_sha256": hashlib.sha256(instrument.encode()).hexdigest(),
            "picks_sha256": hashlib.sha256((instrument if is_trade else "cash").encode()).hexdigest(),
            "calendar_sha256": hashlib.sha256(b"fixture-calendar-not-venue-evidence").hexdigest(),
            "cost_model_sha256": hashlib.sha256(str(fx.get("fee_bps", "3")).encode()).hexdigest(),
            "risk_limits_sha256": str(fx["risk_sha256"]), "decision_at_utc": decision_at.isoformat(),
            "entry_earliest_at_utc": (decision_at + timedelta(milliseconds=int(fx.get("latency_ms", 200)))).isoformat(),
            "exit_scheduled_at_utc": (decision_at + timedelta(hours=8)).isoformat(),
            "data_watermark_at_utc": available_at.isoformat(), "exchange_session_id": f"NSE-{session_date.isoformat()}",
            "holding_sessions": 1, "benchmark_id": str(fx["benchmark_id"]), "capital_inr": "1000000",
            "predicted_return_fraction": "0.01", "predicted_cost_fraction": "0.001",
            "forecast": {"p10_fraction": "-0.01", "p50_fraction": "0.01", "p90_fraction": "0.02",
                         "probability_exceeds_cost": "0.5"},
            "selections": ([{"lineage": instrument, "symbol": instrument, "weight_fraction": "0.01",
                             "quantity": int(fx["quantity"])}] if is_trade else []),
            "exclusions": [], "sources": [{"source_id": event.event_id, "sha256": event_sha,
                                              "published_at_utc": event_time.isoformat(),
                                              "received_at_utc": received_at.isoformat(),
                                              "available_at_utc": available_at.isoformat()}],
            "no_trade_reasons": ["FIXTURE_NO_TRADE"] if no_trade else [],
        }
        runner.record_decision_manifest(session_id=session_id, decision_id=decision_id, manifest=manifest)
        if is_trade:
            runner.record_risk_verdict(
                session_id=session_id, decision_id=decision_id, verdict_id=f"{decision_id}:risk",
                allowed=False, policy_id="fixture-adapter-execution-not-yet-delegated",
                policy_sha256=str(fx["risk_sha256"]), reason_codes=("FIXTURE_EXECUTION_NOT_DELEGATED",),
                assessed_at=decision_at,
            )
        report = runner.close_session(
            session_id, complete=no_trade, now=decision_at + timedelta(seconds=1),
            reason=None if no_trade else "fixture execution and accounting are not yet delegated",
        )
        records = runner.records(session_id)
        return {"session_id": session_id, "state": runner.state_store.state(session_id).value,
                "event_count": len(records["events"]), "decision_count": len(records["decisions"]),
                "paper_intent_count": len(records["intents"]),
                "receipt_count": len(records["execution_receipts"]), "report_status": report["status"],
                "records_sha256": report["records_sha256"], "physical_orders": False,
                "evidence_tier": "LOCAL_FIXTURE"}
    finally:
        runner.close()
        if temporary is not None:
            temporary.cleanup()
