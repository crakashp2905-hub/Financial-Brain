"""Per-stock behaviour: does this name trend or revert, and how much does it move?

Every rule tested in this project so far has been the same rule for every stock. A 5%
hysteresis band was applied to names whose 14-day ATR ranges from 1.4% to 10.6% across the
liquid universe - which makes it a 3.5-sigma band on one name and a 0.5-sigma band on
another. Those are not one rule with one parameter; they are hundreds of different rules,
and the parameter that matters was never chosen.

That is a real defect and this module fixes the inputs for it. Two measurements per name
per session, both strictly backward-looking:

    vr_60           variance ratio over 60 sessions. Above 1, the name trends; below 1,
                    it reverts. Lo & MacKinlay (1988).
    atr_pctile_250  where today's ATR sits in this name's *own* trailing year, so
                    "volatile" means volatile for this stock rather than for the market.

## Why a variance ratio rather than an opinion

VR(k) = Var(k-session return) / (k x Var(1-session return)).

Under a random walk, variance grows linearly with horizon and the ratio is 1. Above 1,
moves persist - a trend rule has something to work with. Below 1, moves reverse - a trend
rule is fighting the name's own behaviour and a reversion rule is not. This is the closest
thing to a direct measurement of "does this stock trend", and it is computable from prices
alone.

## The discipline that makes per-stock adaptation legitimate

Adapting a rule per stock is how strategies are overfitted, and it is also how they are
made sensible. The difference is entirely in *when* the adaptation is computed:

* **Illegitimate** - fit the best band for each name over all history, then evaluate on
  that same history. With ~2,000 names and 2,600 sessions there is always a per-name
  parameter that looks good, and it means nothing.
* **Legitimate** - one fixed formula, identical for every name, whose *inputs* are that
  name's trailing statistics. ``band = k x ATR`` has one free parameter, k, shared across
  the whole universe, and the band still differs per stock because the ATR does.

This module only ever produces the second kind. Nothing here is fitted per name; every
column is a trailing statistic, and the rules that consume them keep a single
universe-wide coefficient.
"""
from __future__ import annotations

VERSION = "bh1"

#: Both measures need the bars and returns already assembled by features/technical.py's
#: panel, so this runs after it and reads the same NSE EQ, factor-adjusted series.
BEHAVIOUR_SQL = """
CREATE OR REPLACE TEMP TABLE _bhbars AS
SELECT p.lineage, p.business_date AS d, p.close_adj AS c,
       e.high_price * p.factor AS h,
       e.low_price  * p.factor AS l,
       p.turnover AS tv
FROM adjusted_prices p
JOIN eod_prices e
  ON e.isin = p.isin AND e.business_date = p.business_date
 AND e.exchange = 'NSE' AND e.series = 'EQ'
WHERE p.close_adj > 0 AND e.high_price > 0 AND e.low_price > 0;

CREATE OR REPLACE TEMP TABLE _bhparts AS
SELECT *,
       ROW_NUMBER() OVER w AS n,
       LN(c / NULLIF(LAG(c) OVER w, 0))     AS r1,
       LN(c / NULLIF(LAG(c, 5) OVER w, 0))  AS r5,
       GREATEST(h - l, ABS(h - LAG(c) OVER w), ABS(l - LAG(c) OVER w)) AS tr
FROM _bhbars
WINDOW w AS (PARTITION BY lineage ORDER BY d);

CREATE OR REPLACE TEMP TABLE _behaviour AS
WITH v AS (
    SELECT lineage, d, n, c,
           VAR_SAMP(r1) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS v1,
           VAR_SAMP(r5) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS v5,
           AVG(tr)      OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14
    FROM _bhparts
    WINDOW w AS (PARTITION BY lineage ORDER BY d)
), ratio AS (
    SELECT lineage, d, n, c, atr14,
           -- VR(5) = Var(5-session) / (5 * Var(1-session)). One above means moves
           -- persist; below means they reverse.
           CASE WHEN n >= 66 AND v1 > 0 THEN v5 / (5 * v1) END AS vr_60,
           CASE WHEN n >= 15 AND c > 0 THEN atr14 / c END      AS atr_pct
    FROM v
)
SELECT lineage, d, vr_60, atr_pct,
       -- The name's own volatility percentile over its trailing year: "volatile" judged
       -- against this stock's history, not against the market's cross-section.
       CASE WHEN n >= 250 THEN
         PERCENT_RANK() OVER (PARTITION BY lineage ORDER BY d
                              ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)
       END AS atr_pctile_250
FROM ratio;
"""

COLUMNS = ("vr_60", "atr_pct", "atr_pctile_250")


def build(con) -> dict:
    """Add the behaviour columns to ``features``. Run after technical.build."""
    for statement in BEHAVIOUR_SQL.strip().split(";\n"):
        if statement.strip():
            con.execute(statement)
    con.execute("""CREATE OR REPLACE TABLE features AS
                   SELECT f.*, b.* EXCLUDE (lineage, d)
                   FROM features f
                   LEFT JOIN _behaviour b
                     ON b.lineage = f.lineage AND b.d = f.business_date""")
    row = con.execute("""SELECT COUNT(*), COUNT(vr_60), MEDIAN(vr_60),
                         AVG(CASE WHEN vr_60 > 1 THEN 1.0 ELSE 0.0 END),
                         MEDIAN(atr_pct)
                         FROM features WHERE adv20 >= 1e7""").fetchone()
    return {"version": VERSION, "liquid_rows": row[0], "with_vr": row[1],
            "median_vr_60": round(float(row[2] or 0), 4),
            "share_trending": round(float(row[3] or 0), 4),
            "median_atr_pct": round(float(row[4] or 0), 5)}
