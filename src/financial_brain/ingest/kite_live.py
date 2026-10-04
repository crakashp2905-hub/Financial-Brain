"""Live prices, snapshotted and checked against what the archive says.

One read-only quote call for a list of symbols, stored with the time it was taken, and a comparison
with the latest close already in the archive. Two jobs:

1. give the scanner something fresher than yesterday's bhavcopy while the market is open, and
2. be a data-quality check that does not depend on the data it checks: if Kite's last price and our
   adjusted close disagree by more than a corporate action could explain, one of them is wrong, and the
   difference is reported instead of both being trusted.

A quote outside market hours returns the last close, so a "live" price equal to the close is the
expected result on a weekend and says nothing about the market; ``market_open`` is reported so a reader
does not mistake it for confirmation.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OPEN, CLOSE = time(9, 15), time(15, 30)
SCHEMA = """
CREATE TABLE IF NOT EXISTS live_quotes (
    taken_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    symbol     VARCHAR NOT NULL,
    last_price DOUBLE,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    prev_close DOUBLE,
    PRIMARY KEY (taken_at, symbol)
);
"""


def market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def snapshot(con, symbols: list[str], *, quote_fn=None, now: datetime | None = None) -> dict:
    from ..providers import kite_data as kd
    quote_fn = quote_fn or kd.quote
    con.execute(SCHEMA)
    now = now or datetime.now(IST)
    q = quote_fn([f"NSE:{s}" for s in symbols], "ohlc")
    rows = []
    for key, v in q.items():
        o = v.get("ohlc") or {}
        rows.append((now, key.split(":", 1)[-1], v.get("last_price"), o.get("open"), o.get("high"),
                     o.get("low"), o.get("close")))
    if rows:
        con.executemany("INSERT OR IGNORE INTO live_quotes VALUES (?,?,?,?,?,?,?)", rows)
    missing = sorted(set(symbols) - {r[1] for r in rows})
    return {"taken_at": now, "stored": len(rows), "missing": missing, "market_open": market_open(now)}


def divergence(con, *, tolerance: float = 0.03) -> list[dict]:
    """Latest snapshot against the archive's latest adjusted close, largest gaps first."""
    rows = con.execute("""
        WITH l AS (SELECT symbol, last_price, taken_at FROM live_quotes
                   WHERE taken_at = (SELECT MAX(taken_at) FROM live_quotes)),
             a AS (SELECT ticker, close_adj, business_date,
                          ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY business_date DESC) rn
                   FROM adjusted_prices WHERE close_adj > 0)
        SELECT l.symbol, l.last_price, a.close_adj, a.business_date,
               l.last_price / a.close_adj - 1 AS gap
        FROM l JOIN a ON a.ticker = l.symbol AND a.rn = 1
        WHERE l.last_price IS NOT NULL ORDER BY ABS(l.last_price / a.close_adj - 1) DESC""").fetchall()
    return [{"symbol": r[0], "live": r[1], "archive_close": r[2], "archive_date": r[3], "gap": r[4],
             "flag": abs(r[4]) > tolerance} for r in rows]
