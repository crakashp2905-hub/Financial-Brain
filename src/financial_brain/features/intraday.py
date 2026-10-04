"""Intraday features from minute bars: what the first hour of a session says.

One row per (instrument, session), built in SQL from ``minute_bars`` and keyed by ``isin`` so it joins
to everything else. Every feature uses only bars at or before its stated minute, and every outcome
starts at that minute, so a feature never sees the return it is scored against.

    gap               open / previous session's last close - 1
    or_breakout       at 10:30: +1 above the 09:15-09:30 high, -1 below its low, else 0
    vwap_dev          at 11:00: close / VWAP(09:15-11:00) - 1
    rvol_30           first-30-minute volume / its mean over the previous 20 sessions
    range_1h          (high - low) / open over 09:15-10:15, a realised-volatility proxy

Outcomes, both **from the 10:30 close**: ``r_to_close`` (to the 15:15 close) and ``r_to_next_close``.

Minutes are minutes-since-midnight in Asia/Kolkata (09:15 = 555), derived with an explicit timezone
conversion; ``hour(ts)`` on a TIMESTAMPTZ follows the connection's time zone and silently misplaces
every bar by 5:30 on a UTC connection.
"""
from __future__ import annotations

M_OPEN, M_OR_END, M_1015, M_1030, M_1100, M_1515 = 555, 570, 615, 630, 660, 915
M_FIRST30 = 585
FEATURES = ("gap", "or_breakout", "vwap_dev", "rvol_30", "range_1h")
OUTCOMES = ("r_to_close", "r_to_next_close")
TABLE = "intraday_features"
MIN_BARS = 300          #: a session with fewer minute bars than this is a data gap, not a session


def build(con) -> int:
    """(Re)materialise ``intraday_features``. Returns the row count."""
    con.execute(f"DROP TABLE IF EXISTS {TABLE}")
    con.execute(f"""
        CREATE TABLE {TABLE} AS
        WITH b AS (
            SELECT instrument_token AS tok, isin, tradingsymbol AS sym,
                   CAST(ts AT TIME ZONE 'Asia/Kolkata' AS DATE) AS d,
                   hour(ts AT TIME ZONE 'Asia/Kolkata') * 60
                     + minute(ts AT TIME ZONE 'Asia/Kolkata') AS m,
                   open, high, low, close, volume
            FROM minute_bars
        ), s AS (
            SELECT tok, ANY_VALUE(isin) AS isin, ANY_VALUE(sym) AS sym, d,
                   COUNT(*) AS bars,
                   ARG_MIN(open, m) AS open0,
                   MAX(high) FILTER (WHERE m < {M_OR_END}) AS or_hi,
                   MIN(low)  FILTER (WHERE m < {M_OR_END}) AS or_lo,
                   ARG_MAX(close, m) FILTER (WHERE m <= {M_1030}) AS c1030,
                   ARG_MAX(close, m) FILTER (WHERE m <= {M_1100}) AS c1100,
                   ARG_MAX(close, m) FILTER (WHERE m <= {M_1515}) AS c1515,
                   ARG_MAX(close, m) AS clast,
                   SUM(volume * (high + low + close) / 3.0) FILTER (WHERE m <= {M_1100})
                     / NULLIF(SUM(volume) FILTER (WHERE m <= {M_1100}), 0) AS vwap_1100,
                   SUM(volume) FILTER (WHERE m < {M_FIRST30}) AS vol30,
                   (MAX(high) FILTER (WHERE m <= {M_1015}) - MIN(low) FILTER (WHERE m <= {M_1015}))
                     / NULLIF(ARG_MIN(open, m), 0) AS range_1h
            FROM b GROUP BY tok, d HAVING COUNT(*) >= {MIN_BARS}
        ), w AS (
            SELECT *, LAG(clast) OVER (PARTITION BY tok ORDER BY d) AS prev_close,
                   LEAD(clast) OVER (PARTITION BY tok ORDER BY d) AS next_close,
                   AVG(vol30) OVER (PARTITION BY tok ORDER BY d
                                    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS vol30_avg
            FROM s
        )
        SELECT tok, isin, sym, d AS session,
               open0 / prev_close - 1 AS gap,
               CASE WHEN c1030 > or_hi THEN 1 WHEN c1030 < or_lo THEN -1 ELSE 0 END AS or_breakout,
               c1100 / vwap_1100 - 1 AS vwap_dev,
               vol30 / NULLIF(vol30_avg, 0) AS rvol_30,
               range_1h,
               c1515 / c1030 - 1 AS r_to_close,
               next_close / c1030 - 1 AS r_to_next_close
        FROM w WHERE prev_close IS NOT NULL
    """)
    return con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
