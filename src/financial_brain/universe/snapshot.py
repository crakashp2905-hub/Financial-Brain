"""C04 - Point-in-time universe snapshots.

What was actually tradable on each date, recorded on that date. Without this, every
backtest silently survivor-biases: the names that fail are exactly the ones missing from
a universe assembled from today's listings.

There is no clean published "NIFTY 500 membership as of date X" series in India, so we
accumulate our own from the bhavcopy - which does include delisted and suspended names
historically.
"""
from __future__ import annotations

from datetime import date


def write_snapshot(con, raw_table: str, business_date: date, exchange: str) -> int:
    """Record the tradable universe implied by one day's bhavcopy."""
    d = business_date.isoformat()

    con.execute(f"""
        DELETE FROM universe_snapshots
        WHERE business_date = DATE '{d}' AND exchange = '{exchange}'
    """)
    con.execute(f"""
        INSERT INTO universe_snapshots
            (business_date, isin, exchange, ticker, series, instrument_type,
             traded_volume, turnover, trades, close_price, tradable)
        SELECT
            DATE '{d}',
            TRIM(ISIN),
            '{exchange}',
            ANY_VALUE(TRIM(TckrSymb)),
            COALESCE(NULLIF(TRIM(SctySrs), ''), '-'),
            ANY_VALUE(TRIM(FinInstrmTp)),
            SUM(TRY_CAST(TtlTradgVol AS BIGINT)),
            SUM(TRY_CAST(TtlTrfVal AS DOUBLE)),
            SUM(TRY_CAST(TtlNbOfTxsExctd AS BIGINT)),
            ANY_VALUE(TRY_CAST(ClsPric AS DOUBLE)),
            TRUE
        FROM {raw_table}
        WHERE TRIM(ISIN) <> ''
        GROUP BY TRIM(ISIN), COALESCE(NULLIF(TRIM(SctySrs), ''), '-')
    """)
    return con.execute(
        f"SELECT COUNT(*) FROM universe_snapshots WHERE business_date = DATE '{d}' "
        f"AND exchange = '{exchange}'"
    ).fetchone()[0]


def as_of(con, on_date: date, *, exchange: str | None = None,
          instrument_type: str = "STK", min_turnover: float | None = None) -> list[dict]:
    """The investable universe on a past date, as it was known then."""
    sql = """
        SELECT isin, exchange, ticker, series, instrument_type,
               traded_volume, turnover, trades, close_price
        FROM universe_snapshots
        WHERE business_date = ? AND tradable
    """
    params: list = [on_date]
    if instrument_type:
        sql += " AND instrument_type = ?"
        params.append(instrument_type)
    if exchange:
        sql += " AND exchange = ?"
        params.append(exchange)
    if min_turnover is not None:
        sql += " AND turnover >= ?"
        params.append(min_turnover)
    sql += " ORDER BY turnover DESC NULLS LAST"

    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def liquidity_buckets(con, on_date: date, exchange: str = "NSE") -> list[dict]:
    """Rank the universe by turnover and bucket it.

    Buckets drive the impact-cost model in ``costs.india`` - outside the top few hundred
    names, impact dominates every other cost, which is exactly where naive Indian
    backtests go wrong.
    """
    cur = con.execute("""
        WITH ranked AS (
            SELECT isin, ticker, turnover,
                   ROW_NUMBER() OVER (ORDER BY turnover DESC NULLS LAST) AS rnk
            FROM universe_snapshots
            WHERE business_date = ? AND exchange = ? AND instrument_type = 'STK'
              AND turnover IS NOT NULL AND turnover > 0
        )
        SELECT isin, ticker, turnover, rnk,
               CASE WHEN rnk <= 100 THEN 'mega'
                    WHEN rnk <= 300 THEN 'large'
                    WHEN rnk <= 750 THEN 'mid'
                    WHEN rnk <= 1500 THEN 'small'
                    ELSE 'micro' END AS bucket
        FROM ranked ORDER BY rnk
    """, [on_date, exchange])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def churn(con, start: date, end: date, exchange: str = "NSE") -> dict:
    """Names present at the start but gone by the end, and vice versa.

    This is the survivorship problem made visible: everything in ``disappeared`` is a name
    a current-universe backtest would never see.
    """
    q = """
        SELECT DISTINCT isin FROM universe_snapshots
        WHERE business_date = ? AND exchange = ? AND instrument_type = 'STK'
    """
    a = {r[0] for r in con.execute(q, [start, exchange]).fetchall()}
    b = {r[0] for r in con.execute(q, [end, exchange]).fetchall()}
    return {
        "start": start, "end": end, "exchange": exchange,
        "at_start": len(a), "at_end": len(b),
        "disappeared": sorted(a - b), "appeared": sorted(b - a),
    }
