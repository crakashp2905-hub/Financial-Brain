"""Forward-edge commands (FE-0143 .. FE-0146): ``python -m financial_brain.forward_edge.cli <command>``.

Commands work on the offline fixture pack only and never contact a broker. Exit status codes:
0 ok | 2 preflight blocked | 3 evidence incomplete | 4 insufficient evidence (report) | 64 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from financial_brain.forward_edge import evidence_stats as ES
from financial_brain.forward_edge import evidence_labels as EL
from financial_brain.forward_edge import pipeline
from financial_brain.forward_edge.fixtures import fixture_pack

EXIT = {"OK": 0, "PREFLIGHT_BLOCKED": 2, "INCOMPLETE": 3, "INSUFFICIENT": 4, "USAGE": 64}


def cmd_preflight(fixture: str | None, fail: list[str]) -> tuple[dict, int]:
    from financial_brain.forward_edge.session_ops import REQUIRED_PREFLIGHT
    results = {k: k not in fail for k in REQUIRED_PREFLIGHT}
    now = datetime(2027, 1, 4, 3, 30, tzinfo=timezone.utc)
    return pipeline.preflight_report(results, now)


def cmd_paper_run(name: str, dry_run: bool) -> tuple[dict, int]:
    if not dry_run:
        return {"error": "non-dry-run needs a broker; this build has none"}, EXIT["USAGE"]
    pack = fixture_pack()
    if name not in pack:
        return {"error": f"unknown fixture {name}", "known": sorted(pack)}, EXIT["USAGE"]
    return pipeline.run_session(pack[name]), EXIT["OK"]


def cmd_close(name: str) -> tuple[dict, int]:
    pack = fixture_pack()
    if name not in pack:
        return {"error": f"unknown fixture {name}"}, EXIT["USAGE"]
    closed = pipeline.close_session(pipeline.run_session(pack[name]))
    return closed, EXIT["OK"] if closed["status"] == "CLOSED" else EXIT["INCOMPLETE"]


def cmd_offline_demo() -> tuple[dict, int]:
    """Run the five required onboarding scenarios using fixtures only."""
    required = ("win", "loss", "no_trade", "feed_outage", "unknown_order")
    pack = fixture_pack()
    scenarios = []
    for name in required:
        result = pipeline.run_session(pack[name])
        closed = pipeline.close_session(result)
        scenarios.append({
            "scenario": name,
            "outcome": result["outcome"],
            "reason": result["reason"],
            "net_pnl_inr": result["net_pnl"],
            "session_status": closed["status"],
            "custody": result["custody"],
            "evidence_class": "SYNTHETIC_FIXTURE",
            "mode": result["mode"],
            "physical_orders": False,
            "ledger_balanced": result["ledger_balanced"],
        })
    expected = {"win": "WIN", "loss": "LOSS", "no_trade": "NO_TRADE",
                "feed_outage": "NO_TRADE", "unknown_order": "UNKNOWN_ORDER"}
    complete = all(row["outcome"] == expected[row["scenario"]]
                   and row["session_status"] == "CLOSED"
                   and row["custody"] == "LOCAL_ONLY"
                   and row["ledger_balanced"] is True
                   and row["physical_orders"] is False
                   for row in scenarios)
    return ({"schema_version": "1.0", "status": "DEMO_COMPLETE" if complete else "DEMO_INCOMPLETE",
             "evidence_class": "SYNTHETIC_FIXTURE", "real_money_enabled": False,
             "promotion_eligible": False, "scenarios": scenarios,
             "note": "Offline onboarding demo only. Synthetic outcomes are not market observations or alpha evidence."},
            EXIT["OK"] if complete else EXIT["INCOMPLETE"])


def cmd_report(as_of: str) -> tuple[dict, int]:
    sessions = [pipeline.run_session(fx) for fx in fixture_pack().values()]
    by_outcome: dict[str, int] = {}
    for s in sessions:
        by_outcome[s["outcome"]] = by_outcome.get(s["outcome"], 0) + 1
    verdict = ES.promotion_verdict(
        daily_net_alpha=[0.001, -0.001, 0.0005, 0.0], eligible_independent_sessions=0, tier=ES.ClaimTier.LOCAL_PAPER,
        net_by_cost_multiplier={1.0: 0.0, 1.5: 0.0, 2.0: 0.0}, best_block=1.0, sample_design_predeclared=False,
        falsification_status="INSUFFICIENT_EVIDENCE")
    report = {"as_of": as_of, "mode": "FIXTURE_REPLAY", "tier": "HISTORICAL_REPLAY",
              "scheduled_sessions": len(sessions), "outcomes": by_outcome,
              "independent_forward_sessions": 0, "benchmark": "FIXTURE_CASH", "coverage": {"sessions_pct": 100.0},
              "missing_data": ["INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED", "REAL_QUOTES_NOT_AVAILABLE"],
              "promotion_verdict": verdict["verdict"], "reasons": verdict["reasons"], "real_money_enabled": False,
              "note": "Fixture data only: this report proves nothing about alpha."}
    return EL.stamp_report(report, "fixture_replay"), EXIT["INSUFFICIENT"]


def cmd_asset_class_report(path: str) -> tuple[dict, int]:
    """Build a reconciled class report from explicit account and class snapshot JSON."""
    from financial_brain.forward_edge.asset_class_edge import (
        AssetClassEdgeError,
        build_asset_class_edge_report,
    )

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"cannot read asset-class evidence: {exc}"}, EXIT["USAGE"]
    if (not isinstance(payload, dict) or not isinstance(payload.get("scheduled_sessions"), list)
            or not isinstance(payload.get("observations"), list)):
        return {"error": "input requires scheduled_sessions and observations arrays"}, EXIT["USAGE"]
    try:
        report = build_asset_class_edge_report(
            scheduled_sessions=payload["scheduled_sessions"], observations=payload["observations"])
    except AssetClassEdgeError as exc:
        return {"error": str(exc)}, EXIT["USAGE"]
    return report, EXIT["OK"] if report["aggregate_pnl_reportable"] else EXIT["INCOMPLETE"]


def cmd_daily_export(account_archive_path: str, runner_journal_path: str,
                     session_id: str, output_dir: str) -> tuple[dict, int]:
    """Write deterministic JSON, CSV, and HTML files from verified paper evidence."""
    from financial_brain.forward_edge.daily_report import (
        DailyReportError,
        build_daily_report,
        write_daily_reports,
    )

    try:
        archive = json.loads(Path(account_archive_path).read_text(encoding="utf-8"))
        report = build_daily_report(account_archive=archive,
                                    runner_journal_path=Path(runner_journal_path),
                                    session_id=session_id)
        paths = write_daily_reports(report, Path(output_dir))
    except (OSError, json.JSONDecodeError, DailyReportError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    return {"report_sha256": report["report_sha256"], "outputs": {
        name: str(path) for name, path in sorted(paths.items())}, "physical_orders": False}, EXIT["OK"]


def cmd_eod_reconcile(account_journal_path: str, reference_snapshot_path: str,
                      gate_journal_path: str) -> tuple[dict, int]:
    """Audit the local account and apply an explicit independent EOD snapshot."""
    from financial_brain.kernel.paper_account_audit import audit_paper_account_journal
    from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
    from financial_brain.kernel.paper_eod_gate import EodGateError, PaperEodReconciliationGate
    from financial_brain.forward_edge.durable import DurableJournal

    try:
        snapshot = json.loads(Path(reference_snapshot_path).read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("reference snapshot must be a JSON object")
        required = ("session_date", "as_of", "reference_positions", "reference_fill_ids", "reference_marks")
        missing = [key for key in required if key not in snapshot]
        if missing:
            raise ValueError(f"reference snapshot missing fields: {', '.join(missing)}")
        journal_state = audit_paper_account_journal(Path(account_journal_path))
        as_of = datetime.fromisoformat(str(snapshot["as_of"]).replace("Z", "+00:00"))
        result = PaperEodReconciliationGate(Path(gate_journal_path)).reconcile(
            session_date=date.fromisoformat(str(snapshot["session_date"])),
            journal_state=journal_state,
            reference_positions=snapshot["reference_positions"],
            reference_fill_ids=snapshot["reference_fill_ids"],
            reference_marks=snapshot["reference_marks"],
            as_of=as_of,
            mark_tolerance=snapshot.get("mark_tolerance", "0.01"),
            repair_signoff=snapshot.get("repair_signoff"),
        )
        account_records = DurableJournal(Path(account_journal_path)).records()
        if not account_records or account_records[0].get("t") != "ACCOUNT_OPEN":
            raise ValueError("account journal must start with ACCOUNT_OPEN")
        opening = account_records[0]
        account = PaperAccount(
            Path(account_journal_path), account_id=str(opening.get("account", "")),
            initial_cash=Decimal(str(opening.get("cash", "NaN"))),
            currency=str(opening.get("currency", "INR")),
            fee_schedule=FeeSchedule.from_dict(opening.get("fee_schedule")),
        )
        if result["status"] == "HALT_MISMATCH":
            account.trip_risk_latch(
                incident_id=str(result["incident_id"]), reason="EOD_RECONCILIATION_MISMATCH",
                occurred_at=as_of,
            )
        result["paper_account_risk_latched"] = account.risk_latched
        result["entries_allowed"] = result["entries_allowed"] and not account.risk_latched
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc), "entries_allowed": False, "physical_orders": False}, EXIT["INCOMPLETE"]
    except EodGateError as exc:
        return {"error": str(exc), "entries_allowed": False, "physical_orders": False}, EXIT["INCOMPLETE"]
    return {**result, "gate_journal": str(Path(gate_journal_path)),
            "physical_orders": False}, EXIT["OK"] if result["entries_allowed"] else EXIT["INCOMPLETE"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fb forward")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight")
    pf.add_argument("--fail", action="append", default=[])
    pr = sub.add_parser("paper-run")
    pr.add_argument("--date", default="2027-01-04")
    pr.add_argument("--fixture", default="win")
    pr.add_argument("--no-dry-run", action="store_true")
    cl = sub.add_parser("close")
    cl.add_argument("--fixture", default="win")
    sub.add_parser("offline-demo", help="run deterministic onboarding scenarios; no network or broker")
    rp = sub.add_parser("report")
    rp.add_argument("--as-of", default="2027-01-04")
    ac = sub.add_parser("asset-class-report")
    ac.add_argument("--input", required=True, help="JSON with scheduled_sessions and class snapshots")
    de = sub.add_parser("daily-export")
    de.add_argument("--account-archive", required=True, help="immutable EOD paper account archive JSON")
    de.add_argument("--runner-journal", required=True, help="hash-chained forward runner journal JSONL")
    de.add_argument("--session-id", required=True)
    de.add_argument("--output-dir", required=True, help="directory for report.json, report.csv, report.html")
    er = sub.add_parser("eod-reconcile", help="reconcile paper account against an independent snapshot")
    er.add_argument("--account-journal", required=True, help="durable local paper account journal JSONL")
    er.add_argument("--reference-snapshot", required=True, help="independent OMS/venue snapshot JSON")
    er.add_argument("--gate-journal", required=True, help="durable EOD halt and evidence JSONL")
    args = p.parse_args(argv)
    if args.cmd == "preflight":
        out, code = cmd_preflight(None, args.fail)
    elif args.cmd == "paper-run":
        out, code = cmd_paper_run(args.fixture, dry_run=not args.no_dry_run)
    elif args.cmd == "close":
        out, code = cmd_close(args.fixture)
    elif args.cmd == "offline-demo":
        out, code = cmd_offline_demo()
    elif args.cmd == "asset-class-report":
        out, code = cmd_asset_class_report(args.input)
    elif args.cmd == "daily-export":
        out, code = cmd_daily_export(args.account_archive, args.runner_journal, args.session_id, args.output_dir)
    elif args.cmd == "eod-reconcile":
        out, code = cmd_eod_reconcile(args.account_journal, args.reference_snapshot, args.gate_journal)
    else:
        out, code = cmd_report(args.as_of)
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
