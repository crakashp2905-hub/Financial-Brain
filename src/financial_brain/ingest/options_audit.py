"""Read-only coverage and quote-quality audit for the local options data plane."""
from __future__ import annotations

from pathlib import Path

import duckdb


def _table(con, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                            [name]).fetchone())


def report(fo_path: Path, quotes_path: Path, index_path: Path | None = None) -> dict:
    out = {"schema": 1, "fo_daily": {"status": "UNAVAILABLE"},
           "live_quotes": {"status": "UNAVAILABLE"},
           "historical_bid_ask_backtest_ready": False,
           "index_daily": {"status": "UNAVAILABLE", "families": []},
           "optionable_families_by_date": [],
           "note": "Daily F&O closes and live snapshots do not reconstruct historical executable bid/ask fills. "
                   "A listed contract is evidence of contract existence, not liquidity or executable capacity."}
    if fo_path.is_file():
        with duckdb.connect(str(fo_path), read_only=True) as con:
            if _table(con, "fo_bhav"):
                summary = con.execute("""
                    SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date),
                           COUNT(*) FILTER (WHERE option_type = 'CE'),
                           COUNT(*) FILTER (WHERE option_type = 'PE')
                    FROM fo_bhav WHERE instrument LIKE 'OPT%'
                """).fetchone()
                n, days, first, last, calls, puts = summary or (0, 0, None, None, 0, 0)
                out["fo_daily"] = {"status": "AVAILABLE" if n else "EMPTY", "rows": n,
                                   "sessions": days, "first": str(first) if first else None,
                                   "last": str(last) if last else None,
                                   "calls": calls, "puts": puts}
                if {"trade_date", "instrument", "symbol", "option_type"}.issubset(
                        {r[0] for r in con.execute("DESCRIBE fo_bhav").fetchall()}):
                    families = con.execute("""
                        SELECT trade_date, symbol,
                               COUNT(*) FILTER (WHERE option_type = 'CE') AS calls,
                               COUNT(*) FILTER (WHERE option_type = 'PE') AS puts,
                               COUNT(DISTINCT expiry) AS expiries,
                               SUM(COALESCE(contracts, 0)) AS contracts,
                               SUM(COALESCE(oi, 0)) AS open_interest
                        FROM fo_bhav WHERE instrument = 'OPTIDX'
                        GROUP BY trade_date, symbol ORDER BY trade_date, symbol
                    """).fetchall() if {"expiry", "contracts", "oi"}.issubset(
                        {r[0] for r in con.execute("DESCRIBE fo_bhav").fetchall()}) else []
                    out["optionable_families_by_date"] = [
                        {"date": str(d), "symbol": s, "calls": int(c), "puts": int(p),
                         "expiries": int(e), "contracts_traded": float(v or 0),
                         "open_interest": float(oi or 0),
                         "liquidity_evidence": "VOLUME_AND_OI_PRESENT" if (v or 0) > 0 and (oi or 0) > 0
                         else "CONTRACTS_EXIST_LIQUIDITY_UNPROVEN"}
                        for d, s, c, p, e, v, oi in families]
    if quotes_path.is_file():
        with duckdb.connect(str(quotes_path), read_only=True) as con:
            if _table(con, "option_quotes"):
                summary = con.execute("""
                    SELECT COUNT(*), COUNT(DISTINCT CAST(received_at AS DATE)),
                           MIN(received_at), MAX(received_at),
                           COUNT(*) FILTER (WHERE book_quality = 'TWO_SIDED')
                    FROM option_quotes
                """).fetchone()
                n, days, first, last, good = summary or (0, 0, None, None, 0)
                out["live_quotes"] = {"status": "AVAILABLE" if n else "EMPTY", "rows": n,
                                      "sessions": days, "first": str(first) if first else None,
                                      "last": str(last) if last else None,
                                      "two_sided": good,
                                      "two_sided_fraction": good / n if n else None}
    if index_path is not None and index_path.is_file():
        with duckdb.connect(str(index_path), read_only=True) as con:
            if _table(con, "index_levels"):
                families = con.execute("""
                    SELECT index_name, MIN(business_date), MAX(business_date), COUNT(*),
                           COUNT(DISTINCT business_date)
                    FROM index_levels WHERE variant = 'PRICE'
                    GROUP BY index_name ORDER BY index_name
                """).fetchall()
                out["index_daily"] = {"status": "AVAILABLE" if families else "EMPTY",
                    "families": [{"index_name": name, "first": str(first), "last": str(last),
                                  "rows": int(rows), "sessions": int(sessions)}
                                 for name, first, last, rows, sessions in families]}
    return out
