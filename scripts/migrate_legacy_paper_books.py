"""Migrate persisted intraday, swing, options and MF events into a separate canonical event journal."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from financial_brain.portfolio.legacy_migration import migrate_legacy_paper_books


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="directory containing paper_live.duckdb, paper_book.duckdb and optional options_paper_forward.duckdb/financial_brain.duckdb")
    parser.add_argument("--ledger", type=Path, required=True,
                        help="new or existing SQLite output path; source DBs are opened read-only")
    args = parser.parse_args()
    report = migrate_legacy_paper_books(args.data_dir, args.ledger)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
