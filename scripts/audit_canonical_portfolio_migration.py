"""Verify legacy source rows against the canonical paper-ledger event payloads."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from financial_brain.portfolio.legacy_migration import audit_legacy_migration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="directory containing paper_live.duckdb and paper_book.duckdb")
    parser.add_argument("--ledger", type=Path, required=True, help="canonical SQLite ledger to audit")
    parser.add_argument("--output", type=Path, help="optional path for sanitized JSON report (no trade-level values)")
    args = parser.parse_args()
    report: dict[str, Any] = audit_legacy_migration(
        args.data_dir, args.ledger, captured_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    ledger = report["ledger"]
    return 0 if all(ledger[key] == 0 for key in (
        "missing_source_events", "mismatched_source_events", "rejected_source_rows",
        "ledger_events_without_scanned_sources",
    )) else 1


if __name__ == "__main__":
    raise SystemExit(main())
