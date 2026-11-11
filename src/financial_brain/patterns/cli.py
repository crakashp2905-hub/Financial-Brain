"""``patterns`` command group: scan, list, explain, paper-demo, audit (CP-0364, CP-0218).

Every subcommand runs offline against the seeded synthetic corpus. There are no
live, broker or order flags anywhere in this parser (CP-0218). Failures return a
nonzero exit code (CP-0364).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from financial_brain.patterns import offline
from financial_brain.patterns.agent import ReadOnlyAgent
from financial_brain.patterns.demo import NOT_EDGE, paper_preview, run_pipeline, synthetic_corpus
from financial_brain.patterns.detectors import discover
from financial_brain.patterns.spec import sha256_hex


def _records() -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    corpus = synthetic_corpus()
    for symbol, bars in sorted(corpus.items()):
        for f in discover(bars):
            event_id = "pev_" + sha256_hex(f"{symbol}|{f.detector}|{f.candidate_known_index}|{f.start_index}".encode())[:16]
            records[event_id] = {
                "event_id": event_id,
                "ticker": symbol,
                "detector": f.detector,
                "state": f.state_at(len(bars) - 1) or "NONE",
                "candidate_known_index": f.candidate_known_index,
                "evidence_tier": "DESCRIPTIVE",
                "trace": dict(f.geometry),
            }
    return records


def cmd_scan(_: argparse.Namespace) -> int:
    result = run_pipeline(synthetic_corpus())
    print(json.dumps({"formations": result["formation_count"], "status": NOT_EDGE}, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    agent = ReadOnlyAgent(_records())
    try:
        out = agent.call("list_patterns", ticker=args.ticker, state=args.state, limit=args.limit)
    except Exception as exc:  # validation errors are reported, not raised
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    agent = ReadOnlyAgent(_records())
    out = agent.call("explain_event", event_id=args.event_id)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 1 if out.get("error") else 0


def cmd_paper_demo(_: argparse.Namespace) -> int:
    preview = paper_preview(synthetic_corpus())
    print(json.dumps({"label": "SYNTHETIC", "preview": preview}, indent=2, sort_keys=True))
    return 0 if preview is not None else 1


def cmd_audit(args: argparse.Namespace) -> int:
    if not Path(args.matrix).exists():
        print(f"ERROR: matrix not found: {args.matrix}", file=sys.stderr)
        return 1
    rows = json.loads(Path(args.matrix).read_text(encoding="utf-8"))["rows"]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"items": len(rows), "by_status": dict(sorted(counts.items()))}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patterns", description="Offline chart-pattern research (paper only).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="run detectors over the synthetic corpus").set_defaults(func=cmd_scan)
    p_list = sub.add_parser("list", help="list stored pattern records")
    p_list.add_argument("--ticker", default=None)
    p_list.add_argument("--state", default=None)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)
    p_explain = sub.add_parser("explain", help="explain one record by event id")
    p_explain.add_argument("event_id")
    p_explain.set_defaults(func=cmd_explain)
    sub.add_parser("paper-demo", help="show one simulated next-session paper preview").set_defaults(func=cmd_paper_demo)
    p_audit = sub.add_parser("audit", help="summarise the completion matrix")
    p_audit.add_argument("--matrix", default="docs/audits/chart-patterns-completion.json")
    p_audit.set_defaults(func=cmd_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if offline.current_mode() != offline.OFFLINE:
        print("ERROR: patterns CLI runs in offline mode only", file=sys.stderr)
        return 2
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
