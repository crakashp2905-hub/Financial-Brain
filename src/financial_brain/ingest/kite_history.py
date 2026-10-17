"""Widen what is pulled from Kite, with the owner's daily session (never automated: see ``scripts/kite_login.cmd``).

    minute   minute bars 2016 -> today for the union of each year's ``top`` most liquid NSE names (the first pull took
             the top 300 a year = 610 names; ``top=1000`` adds roughly 1,400 more). Into ``data/minute_history.duckdb``,
             resumable: each name's existing coverage is skipped. Kite serves only live instruments, so delisted
             names stay missing - survivorship stated, not fixed.
    futures  daily continuous futures (expired contracts stitched by Kite) with open interest, for every F&O
             underlying's near contract, 2015 -> today, into ``data/fo_history.duckdb`` table ``fut_continuous``.

Both run at Kite's historical ceiling (3 requests/second, enforced in ``providers/kite_data``). Run outside market
hours; the live runner uses the same session.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb

from . import kite_minute as KM

FO_DB = "data/fo_history.duckdb"
FUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS fut_continuous (
    symbol VARCHAR NOT NULL, d DATE NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
    oi DOUBLE, observed_at TIMESTAMPTZ, PRIMARY KEY (symbol, d));
"""


def minute_universe(rcon, top: int, since: int = 2016) -> list[tuple[str, str]]:
    return rcon.execute(f"""
        WITH r AS (SELECT p.ticker, p.isin, ROW_NUMBER() OVER (PARTITION BY f.business_date ORDER BY f.adv20 DESC) rk
                   FROM features f JOIN adjusted_prices p USING (lineage, business_date)
                   WHERE f.business_date IN (SELECT MIN(business_date) FROM features WHERE business_date >= '{since}-01-01'
                                             GROUP BY year(business_date)) AND f.adv20 > 0)
        SELECT ticker, ANY_VALUE(isin) FROM r WHERE rk <= ? GROUP BY ticker ORDER BY MIN(rk)""", [top]).fetchall()


def run_minute(names: list[tuple[str, str]], *, start: date = date(2016, 1, 1), path: str = "data/minute_history.duckdb",
               on_progress=None) -> dict:
    from ..providers import kite_data as kd
    cat = {(x.get("tradingsymbol") or "").upper(): x for x in kd.instruments("NSE")
           if (x.get("segment") or "").upper() == "NSE"}
    out = {"done": 0, "missing": 0, "failed": 0, "stored": 0}
    con = duckdb.connect(path)
    try:
        for i, (sym, isin) in enumerate(names, 1):
            row = cat.get((sym or "").upper())
            if not row:
                out["missing"] += 1
                continue
            try:
                res = KM.ingest_symbol(con, sym, int(row["instrument_token"]), start, date.today(), isin=isin)
                out["done"] += 1
                out["stored"] += int(res.get("stored", 0))
            except Exception as e:                      # noqa: BLE001 - one name failing never stops the pull
                out["failed"] += 1
                if on_progress:
                    on_progress(i, len(names), sym, f"FAILED {str(e)[:80]}")
                continue
            if on_progress:
                on_progress(i, len(names), sym, res.get("stored"))
    finally:
        con.close()
    return out


def run_futures(*, start: date = date(2015, 1, 1), path: str = FO_DB, on_progress=None) -> dict:
    from ..providers import kite_data as kd
    nfo = [x for x in kd.instruments("NFO") if x.get("instrument_type") == "FUT"]
    near: dict[str, dict] = {}
    for x in nfo:
        name = x.get("name")
        if name and (name not in near or str(x["expiry"]) < str(near[name]["expiry"])):
            near[name] = x
    con = duckdb.connect(path)
    con.execute(FUT_SCHEMA)
    out = {"underlyings": len(near), "rows": 0, "failed": 0}
    now = datetime.now(timezone.utc)
    try:
        for i, (name, x) in enumerate(sorted(near.items()), 1):
            try:
                rows = []
                y = start.year
                while y <= date.today().year:                  # yearly windows: the day interval caps at 2,000 days
                    a, b = max(start, date(y, 1, 1)), min(date.today(), date(y, 12, 31))
                    rows += kd.candles(int(x["instrument_token"]), a, b, interval="day", continuous=True, oi=True)
                    y += 1
            except Exception as e:                      # noqa: BLE001
                out["failed"] += 1
                if on_progress:
                    on_progress(i, len(near), name, f"FAILED {str(e)[:80]}")
                continue
            vals = [(name, str(r["ts"])[:10], r["open"], r["high"], r["low"], r["close"], r["volume"], r["oi"], now)
                    for r in rows]
            if vals:
                con.executemany("INSERT OR REPLACE INTO fut_continuous VALUES (?,?,?,?,?,?,?,?,?)", vals)
            out["rows"] += len(vals)
            if on_progress:
                on_progress(i, len(near), name, len(vals))
    finally:
        con.close()
    return out
