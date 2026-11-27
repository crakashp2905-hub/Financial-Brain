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
from decimal import Decimal, InvalidOperation
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
    risk_verdicts = {row["record"].get("decision_id"): row["record"] for row in records
                     if row["kind"] == "risk_verdict" and isinstance(row["record"].get("decision_id"), str)}
    session_close = next((row["record"] for row in reversed(records)
                          if row["kind"] == "session_report"), None)
    day_end = session_close.get("day_end_reconciliation") if isinstance(session_close, dict) else None
    missing_fill_order_ids: list[str] = []
    for order_id in intents:
        receipt = receipts.get(order_id)
        try:
            quantity = int(receipt.get("filled_quantity", 0) or 0) if receipt else 0
        except (TypeError, ValueError, OverflowError):
            quantity = 0
        try:
            requested = int(intents[order_id].get("quantity", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            requested = 0
        if (receipt is None or receipt.get("status") not in {"FILLED", "PARTIAL"}
                or quantity <= 0 or requested <= 0 or quantity < requested):
            missing_fill_order_ids.append(order_id)
    missing_fill_order_ids.sort()
    realized_gross = _decimal_or_none(day_end.get("realized_gross_pnl_inr")) if isinstance(day_end, dict) else None
    unrealized_gross = _decimal_or_none(day_end.get("unrealized_gross_pnl_inr")) if isinstance(day_end, dict) else None
    charges = _decimal_or_none(day_end.get("charges_inr")) if isinstance(day_end, dict) else None
    gross_pnl = realized_gross + unrealized_gross if realized_gross is not None and unrealized_gross is not None else None
    net_pnl = (gross_pnl - charges if gross_pnl is not None and charges is not None
               and day_end.get("fees_reconciled") is True else None)
    blocked_signals = sum(
        1 for decision_id, decision in decisions.items()
        if decision.get("outcome") in {"WITHHELD", "DATA_UNUSABLE"}
        or (decision.get("outcome") == "TRADE" and
            (risk_verdicts.get(decision_id) or {}).get("allowed") is False)
    )
    eligible_strategy_ids = config.get("eligible_strategy_ids")
    eligible_count = (len(set(eligible_strategy_ids)) if isinstance(eligible_strategy_ids, list)
                      and eligible_strategy_ids
                      and all(isinstance(value, str) and value.strip() for value in eligible_strategy_ids)
                      else None)
    eligible_status = ("FROZEN_SESSION_CONFIG" if eligible_count is not None else
                       "UNKNOWN: raw session does not carry a frozen eligible-strategy denominator")
    intents_by_decision: dict[str, list[str]] = {}
    for order_id, intent in intents.items():
        decision_id = intent.get("decision_id")
        if isinstance(decision_id, str):
            intents_by_decision.setdefault(decision_id, []).append(order_id)
    strategy_rows: dict[str, dict[str, int]] = {}
    for decision_id, decision in decisions.items():
        strategy_id = decision.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id:
            strategy_id = "UNKNOWN"
        row = strategy_rows.setdefault(strategy_id, {
            "decision_count": 0, "generated_signal_count": 0, "matched_fill_order_count": 0,
            "missing_fill_order_count": 0,
        })
        row["decision_count"] += 1
        if decision.get("outcome") == "TRADE":
            row["generated_signal_count"] += 1
        for order_id in intents_by_decision.get(decision_id, []):
            receipt = receipts.get(order_id)
            try:
                quantity = int(receipt.get("filled_quantity", 0) or 0) if receipt else 0
                requested = int(intents[order_id].get("quantity", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                quantity, requested = 0, 0
            if receipt and quantity > 0:
                row["matched_fill_order_count"] += 1
            if (receipt is None or quantity <= 0 or requested <= 0 or quantity < requested
                    or receipt.get("status") not in {"FILLED", "PARTIAL"}):
                row["missing_fill_order_count"] += 1
    performance_dimensions = {
        "by_strategy": {key: strategy_rows[key] for key in sorted(strategy_rows)},
        "by_asset_class": None,
        "by_decision_origin": None,
        "by_forward_regime": None,
        "realized_vs_valuation_gross_pnl_inr": {
            "realized": day_end.get("realized_gross_pnl_inr") if isinstance(day_end, dict) else None,
            "unrealized_valuation_change": day_end.get("unrealized_gross_pnl_inr")
            if isinstance(day_end, dict) else None,
        },
        "unavailable_dimensions": {
            "asset_class": "Decision manifests and session config do not bind effective-dated asset-class labels.",
            "decision_origin": "Raw decisions do not persist a validated MANUAL/AUTOMATIC origin.",
            "forward_regime": "No frozen point-in-time regime label and source hash are attached to decisions.",
            "strategy_pnl": "The OMS/accounting close is portfolio-level; realized and valuation P&L are not attributed by strategy.",
        },
    }
    proof = {
        "eligible_count": eligible_count,
        "eligible_count_status": eligible_status,
        "generated_signal_count": decision_outcomes.get("TRADE", 0),
        "blocked_signal_count": blocked_signals,
        "matched_fill_count": filled_receipts,
        "missing_fill_count": len(missing_fill_order_ids),
        "missing_fill_order_ids": missing_fill_order_ids,
        "gross_pnl_inr": _decimal_text(gross_pnl),
        "net_pnl_inr": _decimal_text(net_pnl),
        "worst_loss_inr": None,
        "worst_loss_status": "UNKNOWN: raw audit lacks a validated per-trade realized-loss attribution",
        "benchmark_id": config.get("benchmark"),
        "benchmark_comparison": None,
        "benchmark_status": "UNKNOWN: no same-session benchmark return/mark is persisted in raw runner logs",
        "session_close_status": session_close.get("status") if isinstance(session_close, dict) else None,
        "financial_reconciliation_status": day_end.get("reconciled") if isinstance(day_end, dict) else None,
        "performance_dimensions": performance_dimensions,
    }
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
        "one_page_proof": proof,
        "issues": issues,
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def render_one_page_proof(report: dict[str, Any], session: dict[str, Any]) -> str:
    proof = session["one_page_proof"]
    def value(name: str) -> str:
        item = proof.get(name)
        return "Unknown" if item is None else str(item)
    lines = [
        f"# Forward paper session proof — {session['session_id']}", "",
        f"**Exchange date:** {session['exchange_date']}  ",
        f"**Audit status:** {report['status']}  ",
        f"**Raw audit head:** `{session['audit_sequence_head'] or 'none'}`  ",
        f"**Report SHA-256:** `{report['report_sha256']}`", "",
        "| Measure | Result |", "|---|---:|",
        f"| Eligible strategy count | {value('eligible_count')} |",
        f"| Generated trade signals | {proof['generated_signal_count']} |",
        f"| Blocked signals | {proof['blocked_signal_count']} |",
        f"| Matched fills | {proof['matched_fill_count']} |",
        f"| Missing fills | {proof['missing_fill_count']} |",
        f"| Gross P&L (INR) | {value('gross_pnl_inr')} |",
        f"| Net P&L (INR) | {value('net_pnl_inr')} |",
        f"| Worst realized loss (INR) | {value('worst_loss_inr')} |",
        f"| Frozen benchmark | {value('benchmark_id')} |",
        f"| Benchmark comparison | {value('benchmark_comparison')} |", "",
        "## Attribution coverage", "",
        "| Strategy | Decisions | Signals | Matched orders | Missing orders |",
        "|---|---:|---:|---:|---:|",
    ]
    for strategy_id, counts in proof["performance_dimensions"]["by_strategy"].items():
        lines.append(f"| {strategy_id} | {counts['decision_count']} | {counts['generated_signal_count']} | "
                     f"{counts['matched_fill_order_count']} | {counts['missing_fill_order_count']} |")
    realized_valuation = proof["performance_dimensions"]["realized_vs_valuation_gross_pnl_inr"]
    lines.extend([
        "",
        f"Realized gross P&L: {realized_valuation['realized'] or 'Unknown'} INR; "
        f"unrealized valuation change: {realized_valuation['unrealized_valuation_change'] or 'Unknown'} INR.",
        "Asset class, decision origin, regime and strategy-level P&L are shown as unavailable when not sourced.", "",
        "## Evidence gaps", "",
        f"- Eligible count: {proof['eligible_count_status']}",
        f"- Worst loss: {proof['worst_loss_status']}",
        f"- Benchmark: {proof['benchmark_status']}",
        f"- Attribution gaps: {', '.join(proof['performance_dimensions']['unavailable_dimensions'])}",
        f"- Financial reconciliation: {value('financial_reconciliation_status')}",
        f"- Decision lineage valid: {session['decision_quote_lineage_valid']}",
        f"- Raw hash chain valid: {session['raw_hash_chain_valid']}",
    ])
    return "\n".join(lines) + "\n"


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
    parser.add_argument("--proof-dir", type=Path,
                        help="write one Markdown proof page per session into this directory")
    args = parser.parse_args(argv)
    report = run_daily_audit(state_db=args.state_db, journal_db=args.journal_db,
                             exchange_date=args.date)
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    if args.proof_dir:
        args.proof_dir.mkdir(parents=True, exist_ok=True)
        for session in report["sessions"]:
            session_key = hashlib.sha256(session["session_id"].encode("utf-8")).hexdigest()[:16]
            proof_path = args.proof_dir / f"session-{session_key}.md"
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            proof_path.write_text(render_one_page_proof(report, session), encoding="utf-8")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
