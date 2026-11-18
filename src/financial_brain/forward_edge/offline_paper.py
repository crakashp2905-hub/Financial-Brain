"""Offline archived-data forward-session CLI; no network or broker adapter is imported."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError


def _events_from_archive(path: Path, exchange_date: str) -> tuple[list[NormalizedEvent], str]:
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("archive must be UTF-8 JSON") from exc
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("exchange_date") != exchange_date or not isinstance(payload.get("events"), list)):
        raise RunnerError("archive schema/date mismatch; expected schema_version=1 and an events array")
    zone = ZoneInfo("Asia/Kolkata")
    events: list[NormalizedEvent] = []
    for index, row in enumerate(payload["events"]):
        if not isinstance(row, dict):
            raise RunnerError(f"archive event {index} must be an object")
        try:
            event = NormalizedEvent(
                kind=row["kind"], instrument=row["instrument"],
                event_utc=datetime.fromisoformat(row["event_utc"]),
                received_utc=datetime.fromisoformat(row["received_utc"]),
                available_utc=datetime.fromisoformat(row["available_utc"]),
                payload=tuple(sorted((str(k), str(v)) for k, v in row["payload"].items())),
                ingested_utc=datetime.fromisoformat(row["ingested_utc"]) if row.get("ingested_utc") else None,
            )
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise RunnerError(f"archive event {index} is invalid: {exc}") from exc
        if event.event_utc.astimezone(zone).date().isoformat() != exchange_date:
            raise RunnerError(f"archive event {index} falls outside the requested exchange date")
        facts = dict(event.payload)
        if not facts.get("source") or not facts.get("exchange"):
            raise RunnerError(f"archive event {index} is missing source or exchange provenance")
        events.append(event)
    if not events:
        raise RunnerError("archive has no market events")
    return events, digest


def _config_from_plan(plan: dict) -> ForwardRunConfig:
    return ForwardRunConfig(
        session_id=plan["session_id"], exchange_date=plan["exchange_date"],
        cohort_id=plan["cohort_id"], strategy_sha256=plan["strategy_sha256"],
        risk_policy_sha256=plan["risk_policy_sha256"], constraints_sha256=plan["constraints_sha256"],
        ledger_id=plan["ledger_id"], benchmark_id=plan["benchmark_id"],
        instruments=tuple(plan["instruments"]), capital_inr=plan["capital_inr"],
        mode=Mode(plan["mode"]), attempt_id=plan["attempt_id"],
    )


def _offline_run(runner: ForwardSessionRunner, config: ForwardRunConfig,
                 events: list[NormalizedEvent], archive_sha256: str,
                 decision_at: datetime | None = None) -> dict:
    journal = DurableJournal(runner.journal_path)
    existing = [row for row in journal.records() if row.get("session_id") == config.session_id]
    closed = any(row.get("t") == "SESSION_CLOSED" for row in existing)
    if not closed:
        runner.begin(config)
        runner.append_events(config, events)
        runner.record_preflight(config, {
            "approved": True, "status": "PASS", "mode": "LOCAL_ARCHIVE_REPLAY",
            "archive_sha256": archive_sha256,
            "decision_at_utc": decision_at.isoformat() if decision_at else None,
            "checks": {"archive_schema": True, "source_provenance": True, "timestamps": True,
                       "network_access": False, "physical_orders": False},
        })
    result = runner.run(
        config, events, decision_at=decision_at,
        decision_fn=lambda _: {"side": "NONE", "reason": "NO_STRATEGY_ADAPTER_CONFIGURED"},
        risk_fn=lambda _: {"approved": True, "policy": "offline-no-order"},
        execution_fn=lambda *_: {"status": "MISSED", "receipt_id": "unreachable-no-trade",
                                 "fidelity": "SIMULATED_QUOTE"},
    )
    return {**result, "archive_sha256": archive_sha256, "evidence_tier": "HISTORICAL_REPLAY",
            "physical_orders": False, "network_access": False}


def cmd_real_paper(args: argparse.Namespace) -> int:
    """Run the fail-closed no-strategy workflow on a local archived session, without networking."""
    try:
        if not args.confirm_paper_only:
            raise RunnerError("--confirm-paper-only is required")
        day = date.fromisoformat(args.date).isoformat()
        events, archive_sha256 = _events_from_archive(Path(args.archive), day)
        instruments = tuple(sorted({event.instrument for event in events}))
        session_id = f"{day}:{args.cohort}:{args.attempt_id}"
        config = ForwardRunConfig(
            session_id=session_id, exchange_date=day, cohort_id=args.cohort,
            strategy_sha256=hashlib.sha256(b"financial-brain:offline-no-strategy:v1").hexdigest(),
            risk_policy_sha256=hashlib.sha256(b"financial-brain:offline-no-order-risk:v1").hexdigest(),
            constraints_sha256=hashlib.sha256(b"financial-brain:offline-archive-only:v1").hexdigest(),
            ledger_id=args.ledger_id, benchmark_id=args.benchmark, instruments=instruments,
            capital_inr=args.capital, mode=Mode.RESEARCH_REPLAY, attempt_id=args.attempt_id,
        )
        decision_at = max(event.available_utc for event in events)
        result = _offline_run(ForwardSessionRunner(Path(args.journal)), config, events,
                              archive_sha256, decision_at)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc), "physical_orders": False}))
        return 2


def cmd_resume(args: argparse.Namespace) -> int:
    try:
        journal_path = Path(args.journal)
        rows = DurableJournal(journal_path).records()
        plan_row = next((row for row in rows if row.get("t") == "SESSION_PLAN"
                         and row.get("session_id") == args.resume), None)
        if plan_row is None:
            raise RunnerError("no durable session plan matches --resume")
        config = _config_from_plan(plan_row["config"])
        archived_events = [row["event"] for row in rows if row.get("t") == "MARKET_EVENT"
                           and row.get("session_id") == args.resume]
        events = [NormalizedEvent(
            kind=row["kind"], instrument=row["instrument"],
            event_utc=datetime.fromisoformat(row["event_utc"]),
            received_utc=datetime.fromisoformat(row["received_utc"]),
            available_utc=datetime.fromisoformat(row["available_utc"]),
            payload=tuple(tuple(item) for item in row["payload"]),
            ingested_utc=datetime.fromisoformat(row["ingested_utc"]) if row.get("ingested_utc") else None,
        ) for row in archived_events]
        preflight: dict[str, Any] = next((row["result"] for row in rows
                                          if row.get("t") == "PREFLIGHT_RESULT"
                                          and row.get("session_id") == args.resume), {})
        archive_sha = preflight.get("archive_sha256", "")
        decision_at = (datetime.fromisoformat(preflight["decision_at_utc"])
                       if preflight.get("decision_at_utc") else None)
        if not events:
            if not args.archive:
                raise RunnerError("session has no archived events; pass --archive to restore them")
            events, supplied_sha = _events_from_archive(Path(args.archive), config.exchange_date)
            if archive_sha and archive_sha != supplied_sha:
                raise RunnerError("resume archive SHA does not match the frozen preflight")
            archive_sha = supplied_sha
        if decision_at is None and events:
            decision_at = max(event.available_utc for event in events)
        # Reuse the exact frozen plan and events. The runner returns closed sessions unchanged and
        # will stop at UNKNOWN_ORDER if an intent lacks a durable execution receipt.
        result = _offline_run(ForwardSessionRunner(journal_path), config, events, archive_sha, decision_at)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc), "physical_orders": False}))
        return 2


def register_commands(subparsers: Any) -> None:
    forward = subparsers.add_parser("forward", help="offline forward-session paper tools")
    commands = forward.add_subparsers(dest="forward_command", required=True)
    real = commands.add_parser("real-paper", help="run local archived data; network and physical orders disabled")
    real.add_argument("--date", required=True)
    real.add_argument("--cohort", required=True)
    real.add_argument("--archive", required=True)
    real.add_argument("--journal", required=True)
    real.add_argument("--ledger-id", default="offline-ledger")
    real.add_argument("--benchmark", default="CASH")
    real.add_argument("--capital", default="100000")
    real.add_argument("--attempt-id", default="attempt-1")
    real.add_argument("--confirm-paper-only", action="store_true")
    real.set_defaults(fn=cmd_real_paper)

    session = commands.add_parser("session", help="resume a durable archived paper session")
    session.add_argument("--resume", required=True)
    session.add_argument("--journal", required=True)
    session.add_argument("--archive", help="local archive used only if no events were persisted before the crash")
    session.set_defaults(fn=cmd_resume)

