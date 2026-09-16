"""C01 - Security master.

ISIN is the primary key. Symbols change, BSE scrip codes and NSE tokens differ, companies
merge and restructure; ISIN is the only stable join key across Indian market data.

The master is built by *observation*, not declaration: every bhavcopy we ingest extends
the first_seen/last_seen span of each (isin, exchange, ticker, series) listing. A symbol
change therefore appears in the data as one ISIN acquiring a second listing span - a fact
with dates attached, rather than a mapping someone typed in.
"""
from __future__ import annotations

from datetime import date


def upsert_from_bhavcopy(con, raw_table: str, business_date: date, exchange: str) -> None:
    """Fold one day's bhavcopy into securities / security_listings / security_names."""
    d = business_date.isoformat()

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _obs AS
        SELECT DISTINCT
            TRIM(ISIN)                AS isin,
            '{exchange}'              AS exchange,
            TRIM(TckrSymb)            AS ticker,
            NULLIF(TRIM(SctySrs), '') AS series,
            TRIM(FinInstrmId)         AS instrument_id,
            TRIM(FinInstrmTp)         AS instrument_type,
            TRIM(FinInstrmNm)         AS name
        FROM {raw_table}
        WHERE TRIM(ISIN) <> ''
    """)

    # -- securities ---------------------------------------------------------
    con.execute(f"""
        INSERT INTO securities (isin, first_seen, last_seen, instrument_type, status)
        SELECT o.isin, DATE '{d}', DATE '{d}', ANY_VALUE(o.instrument_type), 'active'
        FROM _obs o
        LEFT JOIN securities s USING (isin)
        WHERE s.isin IS NULL
        GROUP BY o.isin
    """)
    con.execute(f"""
        UPDATE securities SET
            first_seen = LEAST(first_seen, DATE '{d}'),
            last_seen  = GREATEST(last_seen, DATE '{d}')
        WHERE isin IN (SELECT isin FROM _obs)
    """)

    # -- listings (symbol history) -----------------------------------------
    con.execute(f"""
        INSERT INTO security_listings
            (isin, exchange, ticker, series, instrument_id, first_seen, last_seen)
        SELECT o.isin, o.exchange, o.ticker, o.series,
               ANY_VALUE(o.instrument_id), DATE '{d}', DATE '{d}'
        FROM _obs o
        LEFT JOIN security_listings l
               ON l.isin = o.isin AND l.exchange = o.exchange
              AND l.ticker = o.ticker
              AND l.series IS NOT DISTINCT FROM o.series
        WHERE l.isin IS NULL
        GROUP BY o.isin, o.exchange, o.ticker, o.series
    """)
    con.execute(f"""
        UPDATE security_listings SET
            first_seen = LEAST(first_seen, DATE '{d}'),
            last_seen  = GREATEST(last_seen, DATE '{d}')
        WHERE (isin, exchange, ticker) IN (
            SELECT isin, exchange, ticker FROM _obs
        )
    """)

    # -- names --------------------------------------------------------------
    con.execute(f"""
        INSERT INTO security_names (isin, exchange, name, first_seen, last_seen)
        SELECT o.isin, o.exchange, o.name, DATE '{d}', DATE '{d}'
        FROM _obs o
        LEFT JOIN security_names n
               ON n.isin = o.isin AND n.exchange = o.exchange AND n.name = o.name
        WHERE n.isin IS NULL AND o.name <> ''
        GROUP BY o.isin, o.exchange, o.name
    """)
    con.execute(f"""
        UPDATE security_names SET
            first_seen = LEAST(first_seen, DATE '{d}'),
            last_seen  = GREATEST(last_seen, DATE '{d}')
        WHERE (isin, exchange, name) IN (SELECT isin, exchange, name FROM _obs)
    """)


# ---------------------------------------------------------------- resolution
def resolve(con, identifier: str, *, on_date: date | None = None) -> list[dict]:
    """Resolve a ticker, ISIN, or exchange instrument id to ISIN-keyed listings.

    Point-in-time aware: pass ``on_date`` to resolve a ticker as it stood on that date,
    which is the only correct way to read a symbol out of a historical document.
    """
    ident = identifier.strip().upper()
    clause = ""
    params: list = [ident, ident, ident]
    if on_date:
        clause = " AND l.first_seen <= ? AND l.last_seen >= ?"
        params += [on_date, on_date]

    sql = f"""
        SELECT l.isin, l.exchange, l.ticker, l.series, l.instrument_id,
               l.first_seen, l.last_seen,
               (SELECT n.name FROM security_names n
                 WHERE n.isin = l.isin ORDER BY n.last_seen DESC LIMIT 1) AS name
        FROM security_listings l
        WHERE (l.isin = ? OR UPPER(l.ticker) = ? OR l.instrument_id = ?){clause}
        ORDER BY l.last_seen DESC
    """
    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def symbol_changes(con) -> list[dict]:
    """ISINs observed under more than one ticker - i.e. symbol changes in the data."""
    cur = con.execute("""
        SELECT isin,
               COUNT(DISTINCT ticker) AS n_tickers,
               STRING_AGG(DISTINCT ticker, ' -> ' ORDER BY ticker) AS tickers,
               MIN(first_seen) AS first_seen,
               MAX(last_seen)  AS last_seen
        FROM security_listings
        GROUP BY isin
        HAVING COUNT(DISTINCT ticker) > 1
        ORDER BY n_tickers DESC, isin
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def cross_listed(con) -> list[dict]:
    """ISINs present on both NSE and BSE - the join the whole system depends on."""
    cur = con.execute("""
        SELECT isin,
               STRING_AGG(DISTINCT exchange, '+' ORDER BY exchange) AS exchanges,
               STRING_AGG(DISTINCT ticker,   '/' ORDER BY ticker)   AS tickers
        FROM security_listings
        GROUP BY isin
        HAVING COUNT(DISTINCT exchange) > 1
        ORDER BY isin
    """)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
