"""Deterministic, versioned features over adjusted prices (C12).

No model and no look-ahead: every window below is ``ROWS BETWEEN n PRECEDING AND CURRENT
ROW`` over a lineage's own sessions, so a feature on date *t* sees prices up to *t* and
nothing later. A change to any definition is a new ``VERSION``, stored beside the old.

Features (all on adjusted closes, NSE EQ):
    ret_1d, ret_5d, ret_20d, ret_60d, ret_250d   simple returns over N sessions
    mom_12_1                                      return from t-250 to t-21 (skips the
                                                  last month, the classic momentum)
    vol_20, vol_60                                annualised stdev of daily log returns
    dist_52w_high                                 close / 250-session high - 1
    above_ma50, above_ma200                       close above its moving average
    adv20                                         average traded value, 20 sessions (Rs)
    n_obs                                         sessions of history available
A feature needing N sessions is NULL until N exist - never computed on a short window.
"""
from __future__ import annotations

VERSION = "f1"

FEATURES_SQL = f"""
CREATE OR REPLACE TABLE features AS
WITH p AS (
    SELECT lineage, business_date, close_adj AS c, turnover,
           ROW_NUMBER() OVER w AS n,
           LN(close_adj / LAG(close_adj) OVER w) AS lr
    FROM adjusted_prices
    WINDOW w AS (PARTITION BY lineage ORDER BY business_date)
)
SELECT lineage, business_date, '{VERSION}' AS version, n AS n_obs,
       c / LAG(c, 1)   OVER w - 1 AS ret_1d,
       c / LAG(c, 5)   OVER w - 1 AS ret_5d,
       c / LAG(c, 20)  OVER w - 1 AS ret_20d,
       c / LAG(c, 60)  OVER w - 1 AS ret_60d,
       c / LAG(c, 250) OVER w - 1 AS ret_250d,
       LAG(c, 21) OVER w / LAG(c, 250) OVER w - 1 AS mom_12_1,
       CASE WHEN n > 20 THEN STDDEV_SAMP(lr) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                             * SQRT(252) END AS vol_20,
       CASE WHEN n > 60 THEN STDDEV_SAMP(lr) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
                             * SQRT(252) END AS vol_60,
       CASE WHEN n >= 250 THEN c / MAX(c) OVER (w ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)
                               - 1 END AS dist_52w_high,
       CASE WHEN n >= 50 THEN c > AVG(c) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)
            END AS above_ma50,
       CASE WHEN n >= 200 THEN c > AVG(c) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)
            END AS above_ma200,
       CASE WHEN n >= 20 THEN AVG(turnover) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
            END AS adv20
FROM p
WINDOW w AS (PARTITION BY lineage ORDER BY business_date)
"""


def build(con) -> dict:
    from . import events, prices
    out = prices.build(con)
    con.execute(FEATURES_SQL)
    out["feature_rows"] = con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    out["events"] = events.build(con)      # adds the disclosure columns
    return out
