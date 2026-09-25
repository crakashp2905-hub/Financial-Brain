"""Minute bars from Kite, which make four excluded strategies testable for the first time.

``evaluation/timeseries.py`` excludes dual_thrust, r_breaker, ghost_trader and
dynamic_breakout_ii on a data fact rather than an opinion: each keys off the session's
opening range, and this archive was daily. Kite serves minute candles, so the exclusion
stops being permanent.

## What the API actually allows, measured rather than assumed

    max window per request   60 days  (the endpoint says so explicitly beyond it)
    history available        at least 5 years back
    rate limit               3 requests/second, enforced in providers/kite_data

So one instrument-year is roughly six requests, and a hundred liquid names over two years
is about 1,300 requests - some seven minutes at the published rate. That is the size of
job this module is built for: large enough to matter, small enough to be re-runnable.

## Idempotent and resumable

An ingest that cannot be interrupted is an ingest nobody runs twice. Rows are inserted
with ``ON CONFLICT DO NOTHING`` on ``(instrument_token, ts)``, and each instrument's
existing coverage is read first so a resumed run fetches only the gap. Killing it midway
and starting again costs the requests already spent, nothing else.

## Timestamps

Kite returns IST with an explicit ``+0530`` offset. They are stored as
``TIMESTAMP WITH TIME ZONE`` rather than being flattened to naive local time - a naive
timestamp is how a session boundary silently moves when anything later reads it in a
different zone, and the opening range is exactly a session boundary.
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_WINDOW_DAYS = 60            # measured: the endpoint refuses more
DEFAULT_YEARS = 2
DEFAULT_UNIVERSE = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    instrument_token BIGINT,
    tradingsymbol    VARCHAR,
    isin             VARCHAR,
    ts               TIMESTAMP WITH TIME ZONE,
    open             DOUBLE,
    high             DOUBLE,
    low              DOUBLE,
    close            DOUBLE,
    volume           BIGINT,
    source           VARCHAR DEFAULT 'KITE',
    PRIMARY KEY (instrument_token, ts)
);
"""


def windows(start: date, end: date, span: int = MAX_WINDOW_DAYS):
    """Split a range into request-sized chunks the endpoint will accept."""
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=span - 1), end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)


MIN_SESSION_COVERAGE = 0.9      # of the sessions actually traded in the window
MIN_HISTORY_SESSIONS = 250      # a year, since we fetch years of minute bars


def liquid_universe(con, n: int = DEFAULT_UNIVERSE, as_of: date | None = None, *,
                    min_coverage: float = MIN_SESSION_COVERAGE,
                    min_history: int = MIN_HISTORY_SESSIONS) -> list[dict]:
    """The n most liquid NSE names by recent turnover, with their ISINs.

    Liquidity is judged on turnover *already observed*, never on the session being
    fetched, so a universe cannot be chosen with hindsight.

    Two filters that are not decoration. A first version ranked on ``AVG(turnover)`` over
    rows present in the window and returned RENTOMOJO - **two sessions of history** - as
    more liquid than HDFC Bank. Fresh listings trade enormous volume on day one, and an
    average over two rows is an average over two rows:

    * **coverage** - the name must have traded most of the sessions in the window. A
      stock present for 2 of 43 is not more liquid than one present for all 43, whatever
      the mean says.
    * **history** - at least a year of sessions, because there is no point spending API
      budget on years of minute bars for something listed last month.

    The statistic is the **median**, not the mean, for the same reason: one listing-day
    or block-deal spike should not define a name's ordinary liquidity.
    """
    day = as_of or con.execute("SELECT MAX(business_date) FROM adjusted_prices").fetchone()[0]
    rows = con.execute("""
        WITH win AS (
            SELECT ticker, isin, turnover, business_date
            FROM adjusted_prices
            WHERE business_date <= ? AND business_date > ? - INTERVAL 60 DAY
              AND ticker IS NOT NULL AND turnover > 0
        ), sessions AS (
            SELECT COUNT(DISTINCT business_date) AS n FROM win
        ), ranked AS (
            SELECT w.ticker, w.isin,
                   MEDIAN(w.turnover)               AS adv,
                   COUNT(DISTINCT w.business_date)  AS present,
                   (SELECT n FROM sessions)         AS total
            FROM win w GROUP BY 1, 2
        ), history AS (
            SELECT ticker, COUNT(*) AS sessions FROM adjusted_prices GROUP BY 1
        )
        SELECT r.ticker, r.isin, r.adv, r.present, r.total, h.sessions
        FROM ranked r JOIN history h USING (ticker)
        WHERE r.present >= ? * r.total AND h.sessions >= ?
        ORDER BY r.adv DESC LIMIT ?""",
        [day, day, min_coverage, min_history, n]).fetchall()
    return [{"tradingsymbol": r[0], "isin": r[1], "adv": r[2],
             "sessions_present": r[3], "sessions_in_window": r[4],
             "history_sessions": r[5]} for r in rows]


def covered(con, instrument_token: int) -> tuple[date | None, date | None]:
    """The span already stored for an instrument, so a resumed run fetches only the gap."""
    row = con.execute("""SELECT MIN(ts)::DATE, MAX(ts)::DATE FROM minute_bars
                         WHERE instrument_token = ?""", [instrument_token]).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _bulk_insert(con, token: int, tradingsymbol: str, isin: str | None,
                 rows: list[dict]) -> int:
    """Load through a newline-delimited JSON staging file.

    executemany binds parameters row by row at roughly 1.2 ms each, which is fine for
    a day of filings and hopeless here: one name-year is ~94,000 minute bars, so a
    hundred names would be hours of binding rather than minutes of fetching. The staging
    file turns it into a single scan.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as fh:
        staging = Path(fh.name)
        for r in rows:
            fh.write(json.dumps({
                "instrument_token": token, "tradingsymbol": tradingsymbol,
                "isin": isin, "ts": r["ts"], "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"],
                "volume": int(r["volume"] or 0)}) + "\n")
    try:
        con.execute(f"""
            INSERT INTO minute_bars
              (instrument_token, tradingsymbol, isin, ts, open, high, low, close,
               volume, source)
            SELECT instrument_token, tradingsymbol, isin, ts::TIMESTAMPTZ,
                   open, high, low, close, volume, 'KITE'
            FROM read_json('{staging.as_posix()}', format='newline_delimited', columns={{
                'instrument_token': 'BIGINT', 'tradingsymbol': 'VARCHAR',
                'isin': 'VARCHAR', 'ts': 'VARCHAR', 'open': 'DOUBLE', 'high': 'DOUBLE',
                'low': 'DOUBLE', 'close': 'DOUBLE', 'volume': 'BIGINT'}})
            ON CONFLICT DO NOTHING""")
    finally:
        staging.unlink(missing_ok=True)
    return len(rows)


def ingest_symbol(con, tradingsymbol: str, token: int, start: date, end: date, *,
                  isin: str | None = None, interval: str = "minute",
                  on_progress=None) -> dict:
    """Fetch and store one instrument's bars over a range, in acceptable windows."""
    from ..providers import kite_data as kd

    con.execute(SCHEMA)
    have_from, have_to = covered(con, token)
    fetched = stored = 0
    for w_start, w_end in windows(start, end):
        # Skip windows already wholly covered; a partial window is refetched, which is
        # cheap and lets ON CONFLICT settle the overlap.
        if have_from and have_to and w_start >= have_from and w_end <= have_to:
            continue
        rows = kd.candles(token, w_start, w_end, interval=interval)
        fetched += len(rows)
        if rows:
            stored += _bulk_insert(con, token, tradingsymbol, isin, rows)
        if on_progress:
            on_progress(tradingsymbol, w_start, w_end, len(rows))
    return {"tradingsymbol": tradingsymbol, "token": token,
            "fetched": fetched, "stored": stored}


def run(con, *, names: int = DEFAULT_UNIVERSE, years: int = DEFAULT_YEARS,
        end: date | None = None, interval: str = "minute", on_progress=None) -> dict:
    """Ingest minute bars for the most liquid names over the last ``years``."""
    from ..providers import kite_data as kd

    con.execute(SCHEMA)
    end = end or date.today()
    start = end - timedelta(days=365 * years)

    universe = liquid_universe(con, names, as_of=end)
    catalogue = kd.instruments("NSE")
    by_symbol = {(r.get("tradingsymbol") or "").strip().upper(): r for r in catalogue
                 if (r.get("segment") or "").upper() == "NSE"}

    done, missing, failed = [], [], []
    for entry in universe:
        sym = (entry["tradingsymbol"] or "").strip().upper()
        row = by_symbol.get(sym)
        if not row:
            missing.append(sym)
            continue
        try:
            done.append(ingest_symbol(con, sym, int(row["instrument_token"]), start, end,
                                      isin=entry["isin"], interval=interval,
                                      on_progress=on_progress))
        except Exception as e:                      # noqa: BLE001 - one bad name is not fatal
            failed.append({"tradingsymbol": sym, "error": str(e)[:200]})
    total = con.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0]
    return {"requested": len(universe), "ingested": len(done),
            "no_instrument_token": missing, "failed": failed,
            "rows_in_table": total,
            "span": {"from": start.isoformat(), "to": end.isoformat()}}


def coverage(con) -> list[dict]:
    """What is stored, per instrument - the answer to "can I test on this yet"."""
    con.execute(SCHEMA)
    rows = con.execute("""SELECT tradingsymbol, COUNT(*), MIN(ts)::DATE, MAX(ts)::DATE,
                                 COUNT(DISTINCT ts::DATE)
                          FROM minute_bars GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    return [{"tradingsymbol": r[0], "bars": r[1], "from": r[2], "to": r[3],
             "sessions": r[4]} for r in rows]


def as_daily(con, tradingsymbol: str) -> list[dict]:
    """Minute bars folded into sessions, with the opening range the intraday systems need.

    ``or_high``/``or_low`` are the first fifteen minutes. dual_thrust, r_breaker and
    dynamic_breakout_ii are all built on some version of that number, and computing it
    once here keeps every strategy reading the same definition.
    """
    rows = con.execute("""
        WITH b AS (
            SELECT ts::DATE AS d, ts, open, high, low, close, volume
            FROM minute_bars WHERE tradingsymbol = ?
        ), ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY d ORDER BY ts) AS n
            FROM b
        )
        SELECT d,
               MIN(ts)                                          AS first_ts,
               ARG_MIN(open, ts)                                AS session_open,
               MAX(high)                                        AS session_high,
               MIN(low)                                         AS session_low,
               ARG_MAX(close, ts)                               AS session_close,
               SUM(volume)                                      AS session_volume,
               MAX(high) FILTER (WHERE n <= 15)                 AS or_high,
               MIN(low)  FILTER (WHERE n <= 15)                 AS or_low,
               COUNT(*)                                         AS minutes
        FROM ranked GROUP BY d ORDER BY d""", [tradingsymbol]).fetchall()
    return [{"business_date": r[0], "first_ts": r[1], "open": r[2], "high": r[3],
             "low": r[4], "close": r[5], "volume": r[6], "or_high": r[7],
             "or_low": r[8], "minutes": r[9]} for r in rows]


__all__ = ["run", "ingest_symbol", "coverage", "as_daily", "liquid_universe",
           "windows", "SCHEMA", "MAX_WINDOW_DAYS", "datetime"]
