"""Classify the legacy intraday missing-exit cases using a read-only canonical ledger."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.portfolio.legacy_exit_adjudication import report_body


DEFAULT_DB = ROOT / "data/canonical_portfolio.sqlite"
DEFAULT_AUDIT = ROOT / "docs/edge-program/canonical-migration-audit.v1.json"
DEFAULT_OUTPUT = ROOT / "docs/edge-program/legacy-intraday-exit-adjudication.v1.json"
ACCOUNT = "intraday:live-v1:live"


def build(source_db: Path, audit_path: Path) -> dict[str, Any]:
    if not source_db.is_file():
        raise FileNotFoundError(f"local canonical paper ledger is unavailable: {source_db}")
    metadata = json.loads(audit_path.read_text(encoding="utf-8"))
    snapshot_as_of = metadata.get("captured_at_utc")
    if not isinstance(snapshot_as_of, str):
        raise ValueError("canonical migration audit lacks a snapshot timestamp")
    datetime.fromisoformat(snapshot_as_of.replace("Z", "+00:00"))
    connection_uri = f"file:{source_db.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(connection_uri, uri=True)) as con:
        con.row_factory = sqlite3.Row
        records = con.execute("""SELECT event_json,payload_sha256 FROM portfolio_events
            WHERE account_id=? ORDER BY sequence""", (ACCOUNT,)).fetchall()
    events: list[dict[str, object]] = []
    for record in records:
        encoded = str(record["event_json"])
        row = json.loads(encoded)
        actual = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if actual != record["payload_sha256"]:
            raise ValueError("canonical event payload hash mismatch; refusing adjudication")
        events.append(row)
    expected_source = metadata.get("sources", {}).get("intraday", {}).get("source_rows")
    unresolved = [row for row in events if row.get("event_kind") == "UNRESOLVED_POSITION"]
    if expected_source is not None and len(unresolved) != 51:
        raise ValueError(f"expected the 51 reported missing-exit rows, found {len(unresolved)}")
    digest = hashlib.sha256(source_db.read_bytes()).hexdigest()
    return report_body(events=events, source_snapshot_sha256=digest, snapshot_as_of_utc=snapshot_as_of)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build(args.source_db, args.audit)
    expected = (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if args.check:
        if not args.output.is_file() or args.output.read_bytes().replace(b"\r\n", b"\n") != expected:
            raise SystemExit("legacy intraday exit adjudication report is missing or stale")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(expected)
    print(f"legacy exit cases={report['case_count']}; counts={report['classification_counts']}; "
          f"pnl_release_authorized={report['pnl_release_authorized']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
