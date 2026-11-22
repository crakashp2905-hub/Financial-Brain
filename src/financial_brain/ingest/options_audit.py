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
                summary = con.execute("""
                    SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date),
                           COUNT(*) FILTER (WHERE option_type = 'CE'),
                           COUNT(*) FILTER (WHERE option_type = 'PE')
                    FROM fo_bhav WHERE instrument LIKE 'OPT%'
                """).fetchone()
                assert summary is not None
                n, days, first, last, calls, puts = summary
                out["fo_daily"] = {"status": "AVAILABLE" if n else "EMPTY", "rows": n,
                                   "sessions": days, "first": str(first) if first else None,
                                   "last": str(last) if last else None,
                                   "calls": calls, "puts": puts}
    if quotes_path.is_file():
        with duckdb.connect(str(quotes_path), read_only=True) as con:
            if _table(con, "option_quotes"):
                summary = con.execute("""
                    SELECT COUNT(*), COUNT(DISTINCT CAST(received_at AS DATE)),
                           MIN(received_at), MAX(received_at),
                           COUNT(*) FILTER (WHERE book_quality = 'TWO_SIDED')
                    FROM option_quotes
                """).fetchone()
                assert summary is not None
                n, days, first, last, good = summary
                out["live_quotes"] = {"status": "AVAILABLE" if n else "EMPTY", "rows": n,
                                      "sessions": days, "first": str(first) if first else None,
                                      "last": str(last) if last else None,
                                      "two_sided": good,
                                      "two_sided_fraction": good / n if n else None}
                columns = {r[0] for r in con.execute("""SELECT column_name
                    FROM information_schema.columns WHERE table_name = 'option_quotes'""").fetchall()}
                needed = {"source_at", "underlying_source_at", "underlying_ltp", "bid", "ask",
                          "open_interest"}
                missing = sorted(needed - columns)
                if not missing:
                    surface_counts = con.execute("""
                        SELECT COUNT(*) FILTER (WHERE open_interest IS NOT NULL),
                               COUNT(*) FILTER (WHERE underlying_source_at IS NOT NULL
                                                AND underlying_ltp > 0),
                               COUNT(*) FILTER (WHERE source_at IS NOT NULL
                                                AND underlying_source_at IS NOT NULL
                                                AND underlying_ltp > 0 AND bid > 0 AND ask >= bid
                                                AND source_at <= received_at
                                                AND underlying_source_at <= received_at
                                                AND ABS(EPOCH(source_at - underlying_source_at)) <= 30)
                        FROM option_quotes""").fetchone()
                    assert surface_counts is not None
                    observed_oi, observed_spot_clock, synchronized = surface_counts
                    out["surface_input_coverage"] = {
                        "schema": "READY",
                        "observed_open_interest": observed_oi,
                        "observed_underlying_clock": observed_spot_clock,
                        "synchronized_two_sided_quotes": synchronized,
                        "synchronized_two_sided_fraction": synchronized / n if n else None,
                    }
                else:
                    out["surface_input_coverage"] = {
                        "schema": "INCOMPLETE", "missing_columns": missing,
                        "synchronized_two_sided_quotes": 0,
                    }
    return out
