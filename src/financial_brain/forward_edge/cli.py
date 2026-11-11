"""Forward-edge commands (FE-0143 .. FE-0146): ``python -m financial_brain.forward_edge.cli <command>``.

Commands work on the offline fixture pack only and never contact a broker. Exit status codes:
0 ok | 2 preflight blocked | 3 evidence incomplete | 4 insufficient evidence (report) | 64 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from financial_brain.forward_edge import evidence_stats as ES
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


def cmd_report(as_of: str) -> tuple[dict, int]:
    sessions = [pipeline.run_session(fx) for fx in fixture_pack().values()]
    by_outcome: dict[str, int] = {}
    for s in sessions:
        by_outcome[s["outcome"]] = by_outcome.get(s["outcome"], 0) + 1
    verdict = ES.promotion_verdict(
        daily_net_alpha=[0.001, -0.001, 0.0005, 0.0], eligible_independent_sessions=0, tier=ES.ClaimTier.LOCAL_PAPER,
        net_by_cost_multiplier={1.0: 0.0, 1.5: 0.0, 2.0: 0.0}, best_block=1.0, sample_design_predeclared=False,
        falsification_status="INSUFFICIENT_EVIDENCE")
    report = {"as_of": as_of, "tier": "LOCAL_PAPER", "scheduled_sessions": len(sessions), "outcomes": by_outcome,
              "independent_forward_sessions": 0, "benchmark": "FIXTURE_CASH", "coverage": {"sessions_pct": 100.0},
              "missing_data": ["INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED", "REAL_QUOTES_NOT_AVAILABLE"],
              "promotion_verdict": verdict["verdict"], "reasons": verdict["reasons"], "real_money_enabled": False,
              "note": "Fixture data only: this report proves nothing about alpha."}
    return report, EXIT["INSUFFICIENT"]


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
    rp = sub.add_parser("report")
    rp.add_argument("--as-of", default="2027-01-04")
    args = p.parse_args(argv)
    if args.cmd == "preflight":
        out, code = cmd_preflight(None, args.fail)
    elif args.cmd == "paper-run":
        out, code = cmd_paper_run(args.fixture, dry_run=not args.no_dry_run)
    elif args.cmd == "close":
        out, code = cmd_close(args.fixture)
    else:
        out, code = cmd_report(args.as_of)
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
