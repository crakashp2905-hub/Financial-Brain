"""The intraday data plane: every live poll kept permanently, and bars of any size rebuilt from it.

The live paper runner polls Kite's full quote for ~200 names every 20 seconds: last price, the day's VWAP
(``average_price``), cumulative volume, open interest, and five levels of the order book. Until now each poll was used
and forgotten. ``store`` appends it to ``data/quote_archive.duckdb`` (one writer: the runner), so the archive grows a
day at a time into a record the minute-bar history does not have - bid, ask, depth and VWAP as they were.

``bars`` rebuilds OHLCV at 1, 5, 15, 30 or 60 minutes from the snapshots (volume from differences in the cumulative
count), and ``breadth`` gives the share of names above their open and above their VWAP at each poll.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

PATH = Path("data") / "quote_archive.duckdb"
FREQS = {1: "1min", 5: "5min", 15: "15min", 30: "30min", 60: "60min"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    ts TIMESTAMP, symbol VARCHAR, ltp DOUBLE, vwap DOUBLE, volume DOUBLE, oi DOUBLE, open DOUBLE, high DOUBLE,
    low DOUBLE, prev_close DOUBLE, bid DOUBLE, ask DOUBLE, bid_qty DOUBLE, ask_qty DOUBLE, depth_bid_qty DOUBLE,
    depth_ask_qty DOUBLE);
"""


def connect(path: Path | None = None, read_only: bool = False):
    path = path or PATH                                 # read at call time, so tests can point it elsewhere
    if read_only:
        return duckdb.connect(str(path), read_only=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def rows(ts: datetime, q: dict) -> list[tuple]:
    out = []
    for key, v in q.items():
        dep = v.get("depth") or {}
        buy, sell = dep.get("buy") or [], dep.get("sell") or []
        o = v.get("ohlc") or {}
        out.append((ts, key.split(":", 1)[-1], v.get("last_price"), v.get("average_price"), v.get("volume"), v.get("oi"),
                    o.get("open"), o.get("high"), o.get("low"), o.get("close"),
                    (buy[0] if buy else {}).get("price"), (sell[0] if sell else {}).get("price"),
                    (buy[0] if buy else {}).get("quantity"), (sell[0] if sell else {}).get("quantity"),
                    sum(x.get("quantity") or 0 for x in buy), sum(x.get("quantity") or 0 for x in sell)))
    return out


def store(con, ts: datetime, q: dict) -> int:
    r = rows(ts, q)
    if r:
        con.executemany("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    return len(r)


def bars(con, symbol: str, day: str, minutes: int = 5) -> pd.DataFrame:
    """OHLCV bars of ``minutes`` from the archived snapshots of one symbol and day."""
    if minutes not in FREQS:
        raise ValueError(f"minutes must be one of {sorted(FREQS)}")
    s = con.execute("""SELECT ts, ltp, volume FROM quotes WHERE symbol = ? AND CAST(ts AS DATE) = ? AND ltp IS NOT NULL
                       ORDER BY ts""", [symbol, day]).df()
    if s.empty:
        return s
    s = s.set_index("ts")
    s["dv"] = s["volume"].diff().clip(lower=0).fillna(0)
    g = s.resample(FREQS[minutes], label="left", closed="left")
    out = pd.DataFrame({"open": g["ltp"].first(), "high": g["ltp"].max(), "low": g["ltp"].min(), "close": g["ltp"].last(),
                        "volume": g["dv"].sum()}).dropna(subset=["open"])
    return out


def breadth(con, day: str) -> pd.DataFrame:
    """Share of names above their open and above their VWAP, poll by poll."""
    return con.execute("""SELECT ts, AVG(CASE WHEN ltp > open THEN 1.0 ELSE 0.0 END) AS above_open,
                                 AVG(CASE WHEN ltp > vwap THEN 1.0 ELSE 0.0 END) AS above_vwap, COUNT(*) AS names
                          FROM quotes WHERE CAST(ts AS DATE) = ? AND ltp IS NOT NULL GROUP BY ts ORDER BY ts""", [day]).df()
