"""Append-only, owner-entered decisions for prospective workflow evaluation.

This log is not a trading ledger or proof of edge. It records what the trader saw
and chose before the outcome is known; no broker API or order path is involved.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/decision_journal.jsonl")
CHOICES = ("NO_TRADE", "WATCH", "PAPER_TRADE", "HOLDING_REVIEW")


def record(path: Path, *, symbol: str, choice: str, asof: str, reason: str,
           shown_at: datetime, decided_at: datetime, evidence: str) -> dict:
    """Record the decision before an outcome exists; reject impossible timestamps."""
    if choice not in CHOICES or not reason.strip() or not evidence.strip():
        raise ValueError("valid choice, reason and evidence are required")
    if shown_at.tzinfo is None or decided_at.tzinfo is None or decided_at < shown_at:
        raise ValueError("timezone-aware decision time must follow shown time")
    if decided_at - shown_at > timedelta(days=1):
        raise ValueError("decision latency cannot exceed one day")
    entry = {"schema": 1, "symbol": symbol.upper().strip(), "choice": choice,
             "asof": asof, "reason": reason.strip(), "evidence": evidence.strip(),
             "shown_at": shown_at.isoformat(), "decided_at": decided_at.isoformat(),
             "latency_seconds": round((decided_at - shown_at).total_seconds(), 1)}
    if not entry["symbol"]:
        raise ValueError("symbol is required; use CASH for a market-wide no-trade decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def report(path: Path) -> dict:
    """Descriptive workflow metrics only; never a profitability verdict."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    latencies = sorted(float(r["latency_seconds"]) for r in rows if "latency_seconds" in r)
    return {"decisions": len(rows), "by_choice": dict(Counter(r["choice"] for r in rows)),
            "median_decision_seconds": (latencies[(len(latencies) - 1) // 2] +
                                        latencies[len(latencies) // 2]) / 2 if latencies else None,
            "note": "Descriptive only. No trade outcome, causal benefit or profitability is implied."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-entered decision journal; no orders")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("record")
    add.add_argument("--symbol", required=True)
    add.add_argument("--choice", choices=CHOICES, required=True)
    add.add_argument("--asof", required=True, help="date/time of data shown")
    add.add_argument("--shown-at", required=True, help="ISO timestamp with timezone")
    add.add_argument("--reason", required=True)
    add.add_argument("--evidence", required=True)
    sub.add_parser("report")
    args = parser.parse_args(argv)
    if args.command == "record":
        shown = datetime.fromisoformat(args.shown_at)
        now = datetime.now(timezone.utc)
        result = record(PATH, symbol=args.symbol, choice=args.choice, asof=args.asof,
                        reason=args.reason, evidence=args.evidence, shown_at=shown, decided_at=now)
    else:
        result = report(PATH)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
