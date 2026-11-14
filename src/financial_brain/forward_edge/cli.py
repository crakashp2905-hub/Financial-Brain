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


def cmd_book_performance_report(input_path: str, output_root: str) -> tuple[dict, int]:
    """Build and persist a content-addressed per-book report from explicit session evidence."""
    from financial_brain.forward_edge.book_performance_report import (
        BookPerformanceError,
        build_book_performance_report,
        write_book_performance_report,
    )

    try:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
            raise ValueError("input JSON must contain a sessions array")
        report = build_book_performance_report(payload["sessions"])
        outputs = write_book_performance_report(report, Path(output_root))
    except (OSError, json.JSONDecodeError, ValueError, BookPerformanceError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    return {**outputs, "physical_orders": False}, EXIT["OK"]


def cmd_registered_portfolio_report(registry_path: str, plan_id: str,
                                   account_root: str) -> tuple[dict, int]:
    """Report all existing journals in one verified frozen paper-book plan."""
    from financial_brain.kernel.paper_portfolio import PaperAccountPortfolio, PaperPortfolioError
    from financial_brain.kernel.registered_paper_books import RegisteredPaperBooks

    try:
        registry = RegisteredPaperBooks(Path(registry_path))
        report = PaperAccountPortfolio.from_registered_plan(
            registry, plan_id=plan_id, account_root=Path(account_root)).report()
    except (OSError, ValueError, PaperPortfolioError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    return {"plan_id": plan_id, **report}, EXIT["OK"]


def cmd_daily_paper_cycle(manifest_path: str) -> tuple[dict, int]:
    """Finalize every frozen-plan account and emit its daily by-book evidence report."""
    from financial_brain.forward_edge.daily_paper_cycle import (
        DailyPaperCycleError,
        run_daily_paper_cycle,
    )

    try:
        result = run_daily_paper_cycle(Path(manifest_path))
    except (OSError, ValueError, TypeError, DailyPaperCycleError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    return result, EXIT["OK"]


def cmd_daily_paper_run_config(config_path: str, session_date: str) -> tuple[dict, int]:
    """Build a daily manifest from verified close receipts, then preflight and finalize it."""
    from financial_brain.forward_edge.daily_paper_cycle import (
        DailyPaperCycleError,
        run_configured_daily_paper_cycle,
    )

    try:
        result = run_configured_daily_paper_cycle(Path(config_path), session_date)
    except (OSError, ValueError, TypeError, DailyPaperCycleError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    return result, EXIT["OK"]


def cmd_daily_paper_preflight(manifest_path: str) -> tuple[dict, int]:
    """Validate a complete daily-cycle manifest without closing or mutating any book."""
    from financial_brain.forward_edge.daily_paper_cycle import (
        DailyPaperCycleError,
        preflight_daily_paper_cycle,
    )

    try:
        result = preflight_daily_paper_cycle(Path(manifest_path))
    except (OSError, ValueError, TypeError, DailyPaperCycleError) as exc:
        return {"error": str(exc), "ready": False, "physical_orders": False}, EXIT["INCOMPLETE"]
    return result, EXIT["OK"]


def cmd_daily_paper_manifest(args: argparse.Namespace) -> tuple[dict, int]:
    """Derive the manifest's per-book identity rows from registered plans and runner journals."""
    from financial_brain.forward_edge.daily_paper_cycle import (
        DailyPaperCycleError,
        build_daily_paper_manifest,
    )

    try:
        result = build_daily_paper_manifest(
            registry_path=Path(args.registry), plan_id=args.plan_id,
            account_root=Path(args.account_root),
            runner_journals=[Path(path) for path in args.runner_journal],
            session_date=args.session_date, captured_at=args.captured_at,
            marks_map_path=Path(args.marks_map) if args.marks_map else None,
            derive_kite_marks=args.derive_kite_marks,
            max_mark_age_seconds=args.max_mark_age_seconds,
            archive_dir=Path(args.archive_dir),
            bundle_root=Path(args.bundle_root), report_output_root=Path(args.report_output_root),
            output_path=Path(args.output),
            history_sessions_path=Path(args.history_sessions) if args.history_sessions else None,
        )
    except (OSError, ValueError, TypeError, DailyPaperCycleError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    return result, EXIT["OK"]


def cmd_daily_finalize(*, account_journal_path: str, session_date: str, captured_at: str,
                       marks_path: str, runner_journal_path: str, session_id: str,
                       archive_dir: str, bundle_root: str) -> tuple[dict, int]:
    """Close a local paper account and create its replay-verified portable evidence bundle."""
    from financial_brain.forward_edge.daily_evidence_bundle import (
        DailyBundleError,
        close_and_bundle_paper_session,
    )
    from financial_brain.forward_edge.durable import DurableJournal
    from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount

    try:
        account_path = Path(account_journal_path)
        records = DurableJournal(account_path).records()
        if not records:
            raise ValueError("account journal is empty")
        opening = records[0]
        if opening.get("t") != "ACCOUNT_OPEN":
            raise ValueError("account journal must begin with ACCOUNT_OPEN")
        marks = json.loads(Path(marks_path).read_text(encoding="utf-8"))
        if not isinstance(marks, dict):
            raise ValueError("close marks input must be a symbol-keyed JSON object")
        account = PaperAccount(
            account_path, account_id=str(opening["account"]),
            initial_cash=Decimal(str(opening["cash"])),
            currency=str(opening["currency"]),
            fee_schedule=FeeSchedule.from_dict(opening.get("fee_schedule")),
        )
        finalized = close_and_bundle_paper_session(
            account=account, session_date=date.fromisoformat(session_date),
            captured_at=datetime.fromisoformat(captured_at), marks=marks,
            archive_dir=Path(archive_dir), runner_journal_path=Path(runner_journal_path),
            session_id=session_id, bundle_root=Path(bundle_root),
        )
    except (OSError, ValueError, KeyError, DailyBundleError) as exc:
        return {"error": str(exc), "physical_orders": False}, EXIT["INCOMPLETE"]
    archive = finalized["account_archive"]
    bundle = finalized["bundle"]
    return {
        "session_id": session_id, "session_date": session_date,
        "account_archive_sha256": archive["archive_sha256"],
        "bundle_manifest_sha256": bundle["manifest_sha256"],
        "bundle_dir": finalized["bundle_dir"],
        "bundle_verified": bundle["verified"], "physical_orders": False,
    }, EXIT["OK"]


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
    bp = sub.add_parser("book-performance-report")
    bp.add_argument("--input", required=True, help="JSON object containing a sessions array")
    bp.add_argument("--output-root", required=True, help="root for immutable content-addressed reports")
    portfolio = sub.add_parser(
        "registered-portfolio-report", help="read-only report for every account in a frozen paper-book plan")
    portfolio.add_argument("--registry", required=True, help="hash-chained registered paper-book plan journal")
    portfolio.add_argument("--plan-id", required=True)
    portfolio.add_argument("--account-root", required=True, help="root containing the allocated account journals")
    cycle = sub.add_parser(
        "daily-paper-cycle", help="finalize all frozen-plan paper books and build the by-book report")
    cycle.add_argument("--manifest", required=True, help="owner-prepared JSON inputs for this session")
    configured_cycle = sub.add_parser(
        "daily-paper-run", help="derive, preflight, and finalize a cycle from verified runner journals")
    configured_cycle.add_argument("--config", required=True, help="owner-managed daily-cycle config JSON")
    configured_cycle.add_argument("--session-date", required=True, help="NSE exchange session date YYYY-MM-DD")
    preflight = sub.add_parser(
        "daily-paper-preflight", help="validate all daily-cycle evidence without mutating accounts")
    preflight.add_argument("--manifest", required=True, help="owner-prepared JSON inputs for this session")
    manifest = sub.add_parser(
        "daily-paper-manifest", help="derive immutable per-book session bindings from verified runner journals")
    manifest.add_argument("--registry", required=True)
    manifest.add_argument("--plan-id", required=True)
    manifest.add_argument("--account-root", required=True)
    manifest.add_argument("--runner-journal", action="append", required=True,
                          help="verified runner journal; repeat for each journal source")
    manifest.add_argument("--session-date", required=True)
    manifest.add_argument("--captured-at", required=True, help="aware ISO-8601 close capture timestamp")
    marks = manifest.add_mutually_exclusive_group(required=True)
    marks.add_argument("--marks-map", help="JSON map from registered strategy IDs to owner mark files")
    marks.add_argument("--derive-kite-marks", action="store_true",
                       help="derive conservative close marks from Kite-tagged durable quote events")
    manifest.add_argument("--max-mark-age-seconds", type=float,
                          help="required staleness ceiling when --derive-kite-marks is used")
    manifest.add_argument("--archive-dir", required=True)
    manifest.add_argument("--bundle-root", required=True)
    manifest.add_argument("--report-output-root", required=True)
    manifest.add_argument("--output", required=True, help="immutable output manifest path")
    manifest.add_argument("--history-sessions", help="optional JSON file containing prior session rows")
    df = sub.add_parser("daily-finalize", help="close a local paper book and export verified evidence")
    df.add_argument("--account-journal", required=True)
    df.add_argument("--session-date", required=True, help="exchange session date in YYYY-MM-DD")
    df.add_argument("--captured-at", required=True, help="aware ISO close-capture timestamp")
    df.add_argument("--marks", required=True, help="JSON file of owner-provided close mark evidence")
    df.add_argument("--runner-journal", required=True)
    df.add_argument("--session-id", required=True)
    df.add_argument("--archive-dir", required=True)
    df.add_argument("--bundle-root", required=True)
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
    elif args.cmd == "book-performance-report":
        out, code = cmd_book_performance_report(args.input, args.output_root)
    elif args.cmd == "registered-portfolio-report":
        out, code = cmd_registered_portfolio_report(args.registry, args.plan_id, args.account_root)
    elif args.cmd == "daily-paper-cycle":
        out, code = cmd_daily_paper_cycle(args.manifest)
    elif args.cmd == "daily-paper-run":
        out, code = cmd_daily_paper_run_config(args.config, args.session_date)
    elif args.cmd == "daily-paper-preflight":
        out, code = cmd_daily_paper_preflight(args.manifest)
    elif args.cmd == "daily-paper-manifest":
        out, code = cmd_daily_paper_manifest(args)
    elif args.cmd == "daily-finalize":
        out, code = cmd_daily_finalize(
            account_journal_path=args.account_journal, session_date=args.session_date,
            captured_at=args.captured_at, marks_path=args.marks,
            runner_journal_path=args.runner_journal, session_id=args.session_id,
            archive_dir=args.archive_dir, bundle_root=args.bundle_root,
        )
    elif args.cmd == "eod-reconcile":
        out, code = cmd_eod_reconcile(args.account_journal, args.reference_snapshot, args.gate_journal)
    else:
        out, code = cmd_report(args.as_of)
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
