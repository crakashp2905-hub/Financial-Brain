"""Adjusted, continuous price history - the layer every feature stands on (C12).

Two corrections turn raw closes into a series a factor can use:

1. **Continuity across ISIN changes.** A face-value split changes the ISIN
   (``isin_successions``). Each security is keyed by its *lineage* - the latest ISIN in
   its succession chain - so Schaeffler's pre-2022 history (INE513A01014) and its
   post-split history (INE513A01022) are one series.
2. **Split / bonus adjustment.** ``adjustment_factors`` holds, per ISIN, the cumulative
   factor of every action on or after each ex-date. A close on date *t* is multiplied by
   the product of all action factors with ex-date *after t*: prices before a 1:5 split
   are divided by 5, so the series has no artificial -80% day. Reported actions supersede
   derived ones in those factors (``detect.rebuild_derived_factors``).

Point-in-time caveat, stated rather than hidden: adjustment uses today's knowledge of
*later* actions. That is correct for comparing prices through time (every backtest
does it), but a feature must still be computed only from prices up to its own date -
which the window functions in ``indicators.py`` guarantee.
"""
from __future__ import annotations

LINEAGE_VIEW = """
CREATE OR REPLACE VIEW security_lineage AS
WITH RECURSIVE chain(isin, lineage, depth) AS (
    SELECT DISTINCT isin, isin, 0 FROM universe_snapshots
    UNION ALL
    SELECT c.isin, s.new_isin, c.depth + 1
    FROM chain c JOIN isin_successions s ON s.old_isin = c.lineage
    WHERE c.depth < 6
)
SELECT isin, arg_max(lineage, depth) AS lineage FROM chain GROUP BY isin
"""

#: One row per lineage and session: NSE EQ close, adjusted for every later action on any
#: ISIN in the lineage.
STEPS_SQL = """
CREATE OR REPLACE TEMP TABLE _lineage AS SELECT * FROM security_lineage;
CREATE OR REPLACE TEMP TABLE _acts AS   -- each action's own factor, re-keyed to the lineage
    SELECT l.lineage, a.effective_from AS ex_date,
           a.price_factor / COALESCE(LEAD(a.price_factor) OVER (
               PARTITION BY a.isin ORDER BY a.effective_from), 1.0) AS factor
    FROM adjustment_factors a JOIN _lineage l USING (isin);
-- cumulative factor applying to every price strictly before ex_date
CREATE OR REPLACE TEMP TABLE _steps AS
    SELECT lineage, ex_date,
           EXP(SUM(LN(factor)) OVER (PARTITION BY lineage ORDER BY ex_date DESC
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS cum
    FROM _acts WHERE factor > 0;
"""

#: One row per lineage and session: NSE EQ close, adjusted for every later action on any
#: ISIN in the lineage. The step function is evaluated per price by an ASOF join (the
#: first ex-date after the price date carries the product of it and every later action):
#: one pass, where a correlated subquery per row would crawl over ~5M rows. Intermediate
#: tables are materialised because DuckDB's binder rejects the ASOF join over the
#: recursive view in a single statement.
ADJUSTED_SQL = """
CREATE OR REPLACE TABLE adjusted_prices AS
WITH raw AS (
    SELECT u.business_date, l.lineage, u.isin, u.ticker, u.close_price, u.turnover,
           u.traded_volume
    FROM universe_snapshots u JOIN _lineage l USING (isin)
    WHERE u.exchange = 'NSE' AND u.series = 'EQ' AND u.close_price > 0
      AND (u.isin LIKE 'INF%' OR substr(u.isin, 8, 2) = '01')
), f AS (
    SELECT r.business_date, r.lineage, r.isin, r.ticker, r.close_price, r.turnover,
           r.traded_volume, COALESCE(s.cum, 1.0) AS adj
    FROM raw r ASOF LEFT JOIN _steps s
      ON r.lineage = s.lineage AND r.business_date < s.ex_date
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY lineage, business_date
                                 ORDER BY turnover DESC) AS rk
    FROM f
)
SELECT business_date, lineage, isin, ticker, close_price AS close_raw,
       close_price * adj AS close_adj, adj AS factor, turnover, traded_volume
FROM ranked WHERE rk = 1
"""


def build(con) -> dict:
    con.execute(LINEAGE_VIEW)
    con.execute(STEPS_SQL)
    con.execute(ADJUSTED_SQL)
    n, lin = con.execute("SELECT COUNT(*), COUNT(DISTINCT lineage) FROM adjusted_prices"
                         ).fetchone()
    return {"rows": n, "lineages": lin}
