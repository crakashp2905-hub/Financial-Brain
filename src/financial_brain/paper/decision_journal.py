"""Append-only, owner-entered decisions for prospective workflow evaluation.

This log is not a trading ledger or proof of edge. It records what the trader saw
and chose before the outcome is known; no broker API or order path is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/decision_journal.jsonl")
CHOICES = ("NO_TRADE", "WATCH", "PAPER_TRADE", "HOLDING_REVIEW")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _snapshot(value: dict) -> tuple[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError("decision-time snapshot must be a nonempty JSON object")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record(path: Path, *, symbol: str, choice: str, asof: str, reason: str,
           shown_at: datetime, decided_at: datetime, evidence: str,
           snapshot: dict | None = None) -> dict:
    """Record the decision before an outcome exists; reject impossible timestamps."""
    if choice not in CHOICES or not reason.strip() or not evidence.strip():
        raise ValueError("valid choice, reason and evidence are required")
    if shown_at.tzinfo is None or decided_at.tzinfo is None or decided_at < shown_at:
        raise ValueError("timezone-aware decision time must follow shown time")
    if decided_at - shown_at > timedelta(days=1):
        raise ValueError("decision latency cannot exceed one day")
    if decided_at > datetime.now(timezone.utc) + timedelta(minutes=1):
        raise ValueError("decision time cannot be in the future")
    try:
        asof_time = datetime.fromisoformat(asof)
    except ValueError as exc:
        raise ValueError("asof must be an ISO date or timezone-aware timestamp") from exc
    if asof_time.tzinfo is None:
        if asof_time.date() > shown_at.date():
            raise ValueError("asof cannot follow the shown date")
    elif asof_time > shown_at:
        raise ValueError("asof cannot follow shown time")
    entry = {"schema": 1, "symbol": symbol.upper().strip(), "choice": choice,
             "asof": asof, "reason": reason.strip(), "evidence": evidence.strip(),
             "shown_at": shown_at.isoformat(), "decided_at": decided_at.isoformat(),
             "latency_seconds": round((decided_at - shown_at).total_seconds(), 1)}
    if snapshot is not None:
        encoded, digest = _snapshot(snapshot)
        entry.update({"schema": 2, "snapshot": json.loads(encoded), "snapshot_sha256": digest})
    entry["decision_id"] = hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if not entry["symbol"]:
        raise ValueError("symbol is required; use CASH for a market-wide no-trade decision")
    if any(row.get("decision_id") == entry["decision_id"] for row in _rows(path)):
        raise ValueError("duplicate decision")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def report(path: Path) -> dict:
    """Descriptive workflow metrics only; never a profitability verdict."""
    rows = _rows(path)
    latencies = sorted(float(r["latency_seconds"]) for r in rows if "latency_seconds" in r)
    return {"decisions": len(rows), "by_choice": dict(Counter(r["choice"] for r in rows)),
            "with_asof_snapshot": sum(bool(r.get("snapshot_sha256")) for r in rows),
            "median_decision_seconds": (latencies[(len(latencies) - 1) // 2] +
                                        latencies[len(latencies) // 2]) / 2 if latencies else None,
            "note": "Descriptive only. No trade outcome, causal benefit or profitability is implied."}


def audit(path: Path) -> dict:
    """Check journal integrity; a local hash is an error check, not tamper-proof attestation."""
    problems = []
    seen = set()
    for number, row in enumerate(_rows(path), 1):
        key = row.get("decision_id")
        if key in seen:
            problems.append(f"line {number}: duplicate decision_id")
        seen.add(key)
        body = {k: v for k, v in row.items() if k != "decision_id"}
        expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if key != expected:
            problems.append(f"line {number}: decision hash mismatch or legacy unsealed entry")
        if "snapshot" in row:
            try:
                _, digest = _snapshot(row["snapshot"])
            except (TypeError, ValueError):
                digest = None
            if digest != row.get("snapshot_sha256"):
                problems.append(f"line {number}: snapshot hash mismatch")
        try:
            shown = datetime.fromisoformat(row["shown_at"])
            decided = datetime.fromisoformat(row["decided_at"])
            if shown.tzinfo is None or decided.tzinfo is None or decided < shown:
                problems.append(f"line {number}: invalid decision timestamps")
        except (KeyError, ValueError):
            problems.append(f"line {number}: invalid decision timestamps")
    return {"decisions": len(seen), "ok": not problems, "problems": problems,
            "note": "Hashes detect accidental edits; they do not prove when a record was created."}


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
    add.add_argument("--snapshot-file", type=Path,
                     help="JSON object captured when the decision was shown; retained with its hash")
    sub.add_parser("report")
    sub.add_parser("audit")
    args = parser.parse_args(argv)
    if args.command == "record":
        shown = datetime.fromisoformat(args.shown_at)
        now = datetime.now(timezone.utc)
        snapshot = json.loads(args.snapshot_file.read_text(encoding="utf-8")) if args.snapshot_file else None
        result = record(PATH, symbol=args.symbol, choice=args.choice, asof=args.asof,
                        reason=args.reason, evidence=args.evidence, shown_at=shown, decided_at=now,
                        snapshot=snapshot)
    elif args.command == "audit":
        result = audit(PATH)
    else:
        result = report(PATH)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
