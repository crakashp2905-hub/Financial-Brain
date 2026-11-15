"""Operator CLI for forward-edge preflight and paper evidence.

The existing intraday engine is invoked only as historical replay here. This CLI does not
turn replay into forward evidence and has no broker-order path.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _preflight(args: argparse.Namespace) -> int:
    from financial_brain.forward_edge.audit import run_audit

    report = run_audit(Path(args.repo))
    report["status_code"] = 0 if report["overall"] == "GREEN" else 2
    report["paper_session_ready"] = False
    report["paper_session_blockers"] = [
        "No integrated ForwardSessionRunner is registered.",
        "Independent pre-entry timestamp custody is not configured." if report["overall"] == "RED" else
        "Preflight GREEN alone does not attest a market session.",
    ]
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"PREFLIGHT {report['overall']} (status {report['status_code']})")
        for check in report["checks"]:
            print(f"  [{check['status']}] {check['name']}: {check['reason']}")
        for reason in report["paper_session_blockers"]:
            print(f"  [BLOCKED] paper_session: {reason}")
    return report["status_code"]


def _paper_run(args: argparse.Namespace) -> int:
    session_date = date.fromisoformat(args.date)
    plan = {"requested_date": session_date.isoformat(), "mode": "RESEARCH_REPLAY",
            "dry_run": not args.execute, "broker_orders": False,
            "warning": "Historical replay is not prospective or independently attested evidence."}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    from financial_brain.paper.intraday_runner import run_replay

    result = run_replay(session_date, n=args.names)
    print(json.dumps({**plan, "result": str(result)}, indent=2))
    return 0


def _paper_close(args: argparse.Namespace) -> int:
    """Check whether the legacy replay run is closed; never upgrades its evidence class."""
    import duckdb

    session_date = date.fromisoformat(args.date).isoformat()
    path = Path(args.ledger)
    if not path.is_file():
        print(json.dumps({"date": session_date, "status": "INCOMPLETE", "idempotent": True,
                          "verified_forward_evidence": False, "reason": "paper ledger does not exist"}, indent=2))
        return 2
    with duckdb.connect(str(path), read_only=True) as con:
        rows = con.execute("SELECT status, finished FROM runs WHERE day = ? AND mode = 'replay'",
                            [session_date]).fetchall()
    if not rows or not rows[0][1]:
        status, reason = "INCOMPLETE", "no completed replay run is recorded"
    else:
        status, reason = "REPLAY_CLOSED", "legacy replay is closed; no prospective custody is inferred"
    print(json.dumps({"date": session_date, "status": status, "idempotent": True,
                      "verified_forward_evidence": False, "reason": reason}, indent=2))
    return 0 if status == "REPLAY_CLOSED" else 2


def _report(args: argparse.Namespace) -> int:
    from financial_brain.forward_edge.audit import run_audit

    report = run_audit(Path(args.repo))
    report["as_of"] = args.as_of
    report["promotion_verdict"] = "NOT_ELIGIBLE" if report["overall"] != "GREEN" else "REVIEW_REQUIRED"
    report["paper_results"] = "not queried: legacy ledger cohorts are not linked to forward custody"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall"] == "GREEN" else 2


def add_parser(subparsers) -> None:
    """Register `fb forward` commands on the main parser."""
    parser = subparsers.add_parser("forward", help="forward-edge preflight and paper evidence")
    commands = parser.add_subparsers(dest="forward_command", required=True)
    preflight = commands.add_parser("preflight", help="read-only fail-closed readiness audit")
    preflight.add_argument("--repo", default=str(_repo()))
    preflight.add_argument("--json", action="store_true")
    preflight.set_defaults(fn=_preflight)
    run = commands.add_parser("paper-run", help="dry-run a dated replay plan; --execute opts into replay")
    run.add_argument("--date", required=True, help="session date, YYYY-MM-DD")
    run.add_argument("--names", type=int, default=200)
    run.add_argument("--execute", action="store_true", help="write historical replay to the paper ledger")
    run.set_defaults(fn=_paper_run)
    close = commands.add_parser("close", help="idempotently inspect replay closure; does not attest forward evidence")
    close.add_argument("--date", required=True)
    close.add_argument("--ledger", default="data/paper_live.duckdb")
    close.set_defaults(fn=_paper_close)
    report = commands.add_parser("report", help="audit promotion readiness as of a date")
    report.add_argument("--as-of", required=True)
    report.add_argument("--repo", default=str(_repo()))
    report.set_defaults(fn=_report)
