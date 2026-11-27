"""Independently verify one day's forward-session decisions against raw SQLite audit logs.

This job uses only the Python standard library. It does not import strategy, research,
backtest, target-generation, or materialized runner-view modules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any


GENESIS = "0" * 64


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def chain_digest(previous: str, sequence: int, session_id: str, record_id: str,
                 kind: str, occurred_at: str, record_sha256: str) -> str:
    body = {"sequence": sequence, "session_id": session_id, "record_id": record_id,
            "kind": kind, "occurred_at": occurred_at, "record_sha256": record_sha256,
            "previous_event_sha256": previous}
    return digest(body)


def _readonly(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    uri = resolved.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _session_configs(state_db: Path, exchange_date: date) -> tuple[dict[str, dict[str, Any]], list[str]]:
    issues: list[str] = []
    sessions: dict[str, dict[str, Any]] = {}
    connection = _readonly(state_db)
    try:
        rows = connection.execute(
            "SELECT session_id,config_sha256,config_json,created_at FROM forward_sessions "
            "ORDER BY created_at,session_id").fetchall()
        for row in rows:
            try:
                config = json.loads(row["config_json"])
                if config.get("exchange_date") == exchange_date.isoformat():
                    if digest(config) != row["config_sha256"]:
                        issues.append(f"{row['session_id']}: frozen session config hash mismatch")
                    sessions[str(row["session_id"])] = config
            except (TypeError, ValueError, json.JSONDecodeError):
                issues.append(f"{row['session_id']}: frozen session config is invalid JSON")
    finally:
        connection.close()
    return sessions, issues


def _raw_rows(journal: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return journal.execute(
        "SELECT sequence,record_id,kind,occurred_at,record_sha256,record_json,"
        "previous_event_sha256,event_sha256 FROM runner_audit_log "
        "WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()


def _verify_session(session_id: str, config: dict[str, Any], rows: list[sqlite3.Row]) -> dict[str, Any]:
    issues: list[str] = []
    chain_valid = True
    lineage_valid = True
    previous = GENESIS
    prior_sequence = 0
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            record = json.loads(row["record_json"])
            canonical_record = canonical(record)
        except (TypeError, ValueError, json.JSONDecodeError):
            issues.append(f"audit row {row['sequence']}: invalid canonical record JSON")
            chain_valid = False
            continue
        record_sha = hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()
        expected_hash = chain_digest(previous, int(row["sequence"]), session_id,
                                     str(row["record_id"]), str(row["kind"]),
                                     str(row["occurred_at"]), str(row["record_sha256"]))
        sequence = int(row["sequence"])
        if (sequence <= prior_sequence or row["previous_event_sha256"] != previous
                or row["record_sha256"] != record_sha or row["event_sha256"] != expected_hash):
            issues.append(f"audit row {row['sequence']}: raw hash chain verification failed")
            chain_valid = False
        if not str(row["record_id"]).startswith(f"{row['kind']}:"):
            issues.append(f"audit row {row['sequence']}: record ID is not kind-scoped")
            chain_valid = False
        records.append({"kind": str(row["kind"]), "record_id": str(row["record_id"]),
                        "occurred_at": str(row["occurred_at"]), "record": record,
                        "record_sha256": record_sha})
        prior_sequence = sequence
        previous = str(row["event_sha256"])

    if not rows:
        issues.append("session has no raw audit records")
    events: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    receipts: dict[str, dict[str, Any]] = {}
    forecasts: dict[str, dict[str, Any]] = {}
    exposure_records: dict[str, dict[str, Any]] = {}
    resolved_exposures: set[str] = set()

    for row in records:
        kind, body = row["kind"], row["record"]
        if kind == "market_event":
            event = body.get("event")
            if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
                issues.append(f"{row['record_id']}: raw market event missing identity")
                lineage_valid = False
                continue
            event_id = event["event_id"]
            if event_id in events:
                issues.append(f"{row['record_id']}: duplicate raw market event ID {event_id}")
                lineage_valid = False
                continue
            event_sha = digest(event)
            if body.get("event_sha256") != event_sha:
                issues.append(f"{row['record_id']}: embedded market event hash mismatch")
                lineage_valid = False
            if not _aware(event.get("available_at")):
                issues.append(f"{row['record_id']}: market event availability clock is missing/naive")
                lineage_valid = False
            events[event_id] = {"event": event, "sha256": event_sha}
        elif kind == "decision":
            decision_id = body.get("decision_id")
            if not isinstance(decision_id, str) or decision_id in decisions:
                issues.append(f"{row['record_id']}: missing or duplicate decision identity")
                lineage_valid = False
                continue
            decisions[decision_id] = body
        elif kind == "decision_manifest":
            decision_id = body.get("decision_id")
            manifest = body.get("manifest")
            if not isinstance(decision_id, str) or not isinstance(manifest, dict):
                issues.append(f"{row['record_id']}: malformed decision manifest")
                lineage_valid = False
                continue
            if digest(manifest) != body.get("manifest_sha256"):
                issues.append(f"{row['record_id']}: decision manifest hash mismatch")
                lineage_valid = False
            manifests[decision_id] = manifest
        elif kind == "execution_receipt":
            order_id = body.get("order_id")
            if not isinstance(order_id, str) or order_id in receipts:
                issues.append(f"{row['record_id']}: missing or duplicate execution receipt identity")
                lineage_valid = False
                continue
            receipts[order_id] = body
        elif kind == "expected_value_forecast":
            decision_id = body.get("decision_id")
            if isinstance(decision_id, str):
                forecasts[decision_id] = body
        elif kind == "unresolved_exposure":
            exposure_id = body.get("exposure_id")
            if isinstance(exposure_id, str):
                exposure_records[exposure_id] = body
        elif kind == "unresolved_exposure_resolution":
            exposure_id = body.get("exposure_id")
            if isinstance(exposure_id, str):
                resolved_exposures.add(exposure_id)

    decision_outcomes: Counter[str] = Counter()
    input_event_count = 0
    for decision_id, decision in decisions.items():
        outcome = str(decision.get("outcome", "UNKNOWN"))
        decision_outcomes[outcome] += 1
        inputs = decision.get("input_event_ids", [])
        if not isinstance(inputs, list):
            issues.append(f"decision {decision_id}: input_event_ids is not a list")
            lineage_valid = False
            continue
        if not inputs and outcome != "DATA_UNUSABLE":
            issues.append(f"decision {decision_id}: no source event IDs")
            lineage_valid = False
        decision_at = _aware(decision.get("decided_at"))
        if decision_at is None:
            issues.append(f"decision {decision_id}: decision clock is missing/naive")
            lineage_valid = False
        for event_id in inputs:
            source = events.get(event_id)
            if source is None:
                issues.append(f"decision {decision_id}: source event {event_id} absent from raw logs")
                lineage_valid = False
                continue
            input_event_count += 1
            available_at = _aware(source["event"].get("available_at"))
            if decision_at is not None and available_at is not None and available_at > decision_at:
                issues.append(f"decision {decision_id}: input {event_id} became available after decision")
                lineage_valid = False

        manifest = manifests.get(decision_id)
        if outcome == "TRADE" and manifest is None:
            issues.append(f"decision {decision_id}: TRADE has no canonical manifest in raw audit")
            lineage_valid = False
        if manifest is not None:
            sources = manifest.get("sources", [])
            if not isinstance(sources, list) or not sources:
                issues.append(f"decision {decision_id}: manifest has no source lineage")
                lineage_valid = False
            else:
                for source in sources:
                    source_id = source.get("source_id") if isinstance(source, dict) else None
                    event_ref = events.get(source_id) if isinstance(source_id, str) else None
                    if event_ref is None:
                        issues.append(f"decision {decision_id}: manifest source {source_id!r} absent from raw logs")
                        lineage_valid = False
                        continue
                    if source.get("sha256") != event_ref["sha256"]:
                        issues.append(f"decision {decision_id}: manifest source {source_id} hash mismatch")
                        lineage_valid = False
                    for field in ("available_at_utc", "received_at_utc"):
                        source_clock = _aware(source.get(field))
                        event_clock = _aware(event_ref["event"].get(
                            "available_at" if field == "available_at_utc" else "received_at"))
                        if source_clock is not None and event_clock is not None and source_clock != event_clock:
                            issues.append(f"decision {decision_id}: manifest source {source_id} {field} mismatch")
                            lineage_valid = False
                        if source_clock is None or event_clock is None:
                            issues.append(f"decision {decision_id}: manifest source {source_id} {field} missing/naive")
                            lineage_valid = False
        if decision_id in forecasts and outcome == "TRADE":
            forecast_at = _aware(forecasts[decision_id].get("forecasted_at_utc"))
            if decision_at is not None and forecast_at is not None and forecast_at > decision_at:
                issues.append(f"decision {decision_id}: forecast published after decision")
                lineage_valid = False
            if decision_at is None or forecast_at is None:
                issues.append(f"decision {decision_id}: forecast or decision clock missing/naive")
                lineage_valid = False

    intents = {row["record"].get("order_id"): row["record"] for row in records
               if row["kind"] == "paper_intent" and isinstance(row["record"].get("order_id"), str)}
    filled_receipts = 0
    for order_id, receipt in receipts.items():
        try:
            filled_quantity = int(receipt.get("filled_quantity", 0))
        except (TypeError, ValueError, OverflowError):
            filled_quantity = -1
            issues.append(f"receipt {order_id}: filled quantity is malformed")
            lineage_valid = False
        if filled_quantity < 0:
            continue
        if receipt.get("status") in {"FILLED", "PARTIAL"} and filled_quantity > 0:
            filled_receipts += 1
            if order_id not in intents:
                issues.append(f"receipt {order_id}: filled receipt has no raw paper intent")
                lineage_valid = False
        provenance = receipt.get("quote_provenance")
        if not isinstance(provenance, dict):
            if receipt.get("status") in {"FILLED", "PARTIAL"}:
                issues.append(f"receipt {order_id}: fill has no raw quote provenance")
                lineage_valid = False
            continue
        event_id = provenance.get("event_id")
        source = events.get(event_id) if isinstance(event_id, str) else None
        if source is None:
            issues.append(f"receipt {order_id}: quote event {event_id!r} absent from raw logs")
            lineage_valid = False
            continue
        event = source["event"]
        if event.get("kind") != "quote" or provenance.get("event_sha256") != source["sha256"]:
            issues.append(f"receipt {order_id}: quote kind or raw event hash mismatch")
            lineage_valid = False
        for field, event_field in (("event_time_utc", "event_time"),
                                   ("received_at_utc", "received_at"),
                                   ("available_at_utc", "available_at")):
            quote_clock = _aware(provenance.get(field))
            event_clock = _aware(event.get(event_field))
            if quote_clock is None or event_clock is None or quote_clock != event_clock:
                issues.append(f"receipt {order_id}: quote {field} does not match raw event")
                lineage_valid = False
        available_at = _aware(event.get("available_at"))
        simulated_at = _aware(receipt.get("simulated_at"))
        if available_at is not None and simulated_at is not None and available_at > simulated_at:
            issues.append(f"receipt {order_id}: execution precedes quote availability")
            lineage_valid = False
        if available_at is None or simulated_at is None:
            issues.append(f"receipt {order_id}: quote availability or execution clock missing/naive")
            lineage_valid = False

    open_exposures = sorted(set(exposure_records) - resolved_exposures)
    return {
        "session_id": session_id,
        "exchange_date": config.get("exchange_date"),
        "config_sha256": digest(config),
        "audit_record_count": len(rows),
        "audit_sequence_head": rows[-1]["event_sha256"] if rows else None,
        "raw_market_event_count": len(events),
        "decision_count": len(decisions),
        "decision_outcomes": dict(sorted(decision_outcomes.items())),
        "decision_input_reference_count": input_event_count,
        "manifest_count": len(manifests),
        "execution_receipt_count": len(receipts),
        "filled_receipt_count": filled_receipts,
        "open_unresolved_exposure_count": len(open_exposures),
        "open_unresolved_exposure_ids": open_exposures,
        "raw_hash_chain_valid": chain_valid,
        "decision_quote_lineage_valid": lineage_valid,
        "issues": issues,
    }


def run_daily_audit(*, state_db: Path, journal_db: Path, exchange_date: date) -> dict[str, Any]:
    sessions, config_issues = _session_configs(state_db, exchange_date)
    journal = _readonly(journal_db)
    try:
        reports = []
        for session_id, config in sessions.items():
            reports.append(_verify_session(session_id, config, _raw_rows(journal, session_id)))
    finally:
        journal.close()
    issues = list(config_issues)
    if not sessions:
        issues.append("no forward sessions found for the requested exchange date")
    all_session_issues = [f"{row['session_id']}: {issue}" for row in reports for issue in row["issues"]]
    issues.extend(all_session_issues)
    body = {
        "schema": "fb.forward-session-independent-daily-audit.v1",
        "exchange_date": exchange_date.isoformat(),
        "session_count": len(reports),
        "decision_count": sum(row["decision_count"] for row in reports),
        "raw_market_event_count": sum(row["raw_market_event_count"] for row in reports),
        "execution_receipt_count": sum(row["execution_receipt_count"] for row in reports),
        "filled_receipt_count": sum(row["filled_receipt_count"] for row in reports),
        "open_unresolved_exposure_count": sum(row["open_unresolved_exposure_count"] for row in reports),
        "status": "PASS" if reports and not issues else "FAIL",
        "sessions": reports,
        "issues": issues,
        "method": "stdlib-only raw audit SQLite verification; no research/backtest imports",
    }
    return body | {"report_sha256": digest(body)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-db", required=True, type=Path,
                        help="read-only forward session state database")
    parser.add_argument("--journal-db", required=True, type=Path,
                        help="read-only forward session raw audit journal database")
    parser.add_argument("--date", required=True, type=date.fromisoformat,
                        help="exchange date in YYYY-MM-DD")
    parser.add_argument("--output", type=Path, help="write JSON report here; stdout if omitted")
    args = parser.parse_args(argv)
    report = run_daily_audit(state_db=args.state_db, journal_db=args.journal_db,
                             exchange_date=args.date)
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
