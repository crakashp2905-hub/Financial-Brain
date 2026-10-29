"""Read-only coverage and quote-quality audit for the local options data plane."""
from __future__ import annotations

from pathlib import Path

import duckdb


def _table(con, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                            [name]).fetchone())


def report(fo_path: Path, quotes_path: Path) -> dict:
    out = {"schema": 1, "fo_daily": {"status": "UNAVAILABLE"},
           "live_quotes": {"status": "UNAVAILABLE"},
           "historical_bid_ask_backtest_ready": False,
           "note": "Daily F&O closes and live snapshots do not reconstruct historical executable bid/ask fills."}
    if fo_path.is_file():
        with duckdb.connect(str(fo_path), read_only=True) as con:
            if _table(con, "fo_bhav"):
                n, days, first, last, calls, puts = con.execute("""
                    SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date),
                           COUNT(*) FILTER (WHERE option_type = 'CE'),
                           COUNT(*) FILTER (WHERE option_type = 'PE')
                    FROM fo_bhav WHERE instrument LIKE 'OPT%'
                """).fetchone()
                out["fo_daily"] = {"status": "AVAILABLE" if n else "EMPTY", "rows": n,
                                   "sessions": days, "first": str(first) if first else None,
                                   "last": str(last) if last else None,
                                   "calls": calls, "puts": puts}
    if quotes_path.is_file():
        with duckdb.connect(str(quotes_path), read_only=True) as con:
            if _table(con, "option_quotes"):
                n, days, first, last, good = con.execute("""
                    SELECT COUNT(*), COUNT(DISTINCT CAST(received_at AS DATE)),
                           MIN(received_at), MAX(received_at),
                           COUNT(*) FILTER (WHERE book_quality = 'TWO_SIDED')
                    FROM option_quotes
                """).fetchone()
                out["live_quotes"] = {"status": "AVAILABLE" if n else "EMPTY", "rows": n,
                                      "sessions": days, "first": str(first) if first else None,
                                      "last": str(last) if last else None,
                                      "two_sided": good,
                                      "two_sided_fraction": good / n if n else None}
    return out
