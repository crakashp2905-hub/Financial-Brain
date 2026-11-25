"""Weekly coverage check of the intraday archives (read-only; run after the close).

Reports, per store: sessions recorded, first and last session, instruments on the last session and
how many rows carry open interest. The F&O depth/OI study needs 120 sessions in
``data/depth_archive.duckdb`` before any test is allowed.

Usage: python scripts/archive_coverage.py [--root D:/Finance-Brain]
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

NEED = 120
STORES = {"depth_archive (F&O stocks, near futures, index options)": "data/depth_archive.duckdb",
          "quote_archive (paper runner, 200 equities)": "data/quote_archive.duckdb"}


def coverage(path: Path) -> dict:
    if not path.is_file():
        return {"sessions": 0, "status": "MISSING: never written"}
    c = duckdb.connect(str(path), read_only=True)
    try:
        s, first, last = c.execute("SELECT COUNT(DISTINCT CAST(ts AS DATE)), MIN(ts), MAX(ts) FROM quotes").fetchone()
        n_last, oi = c.execute("""SELECT COUNT(DISTINCT symbol), COUNT(*) FILTER (WHERE oi > 0) FROM quotes
                                  WHERE CAST(ts AS DATE) = (SELECT MAX(CAST(ts AS DATE)) FROM quotes)""").fetchone()
    finally:
        c.close()
    return {"sessions": s, "first": str(first)[:10], "last": str(last)[:10], "instruments_last": n_last,
            "oi_rows_last": oi}


def main(root: Path) -> int:
    ok = True
    for name, rel in STORES.items():
        r = coverage(root / rel)
        line = ", ".join(f"{k}={v}" for k, v in r.items())
        print(f"{name}: {line}")
        if rel.startswith("data/depth"):
            print(f"  F&O study clock: {r['sessions']}/{NEED} sessions")
            ok = r["sessions"] > 0
    return 0 if ok else 2


if __name__ == "__main__":
    root = Path(sys.argv[sys.argv.index("--root") + 1]) if "--root" in sys.argv else Path.cwd()
    sys.exit(main(root))
