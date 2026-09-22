"""Evaluation benchmark (P2-3): does a signal predict anything, honestly measured?

For a feature and a horizon *h*, on each rebalance date *t*:

* the universe is lineages with a feature value on *t* and 20-day average traded value
  of at least ``min_adv`` - known on *t*, so the filter adds no look-ahead;
* the outcome is the adjusted return from close *t* to close *t+h sessions* of the
  *same lineage*. A name with no *t+h* price (delisted, suspended) is **not dropped** -
  dropping it is survivorship bias, since such names skew to disasters. It is scored at
  its last traded price inside the window, or flat if it never traded again, and the
  count is reported as ``filled``. (fw1 runs before 2026-09-19 evening dropped them.)
* the score is the Spearman rank IC between feature and outcome, and the top-minus-
  bottom quintile return spread.

Rebalance dates are spaced *h* sessions apart so outcomes do not overlap; the t-stat of
the mean IC is then not inflated by autocorrelation. Every number here is a fact about
the past, not advice.
"""
from __future__ import annotations

import math

FEATURES = {"ret_1d", "ret_5d", "ret_20d", "ret_60d", "ret_250d", "mom_12_1", "vol_20",
            "vol_60", "dist_52w_high",
            # event features (features/events.py): what the company disclosed, counted
            # from the session the news could first have been traded on
            "news_5d", "news_20d", "insider_60d", "adverse_60d", "days_since_news",
            # the System One timing model's own score, fitted point in time
            "timing_score"}


def evaluate(con, feature: str, horizon: int = 20, *, min_adv: float = 1e7,
             start: str | None = None, end: str | None = None, direction: int = 1) -> dict:
    """``direction=-1`` states the hypothesis "low is good" (e.g. low volatility): the
    signal is ranked reversed, so a positive IC always means the hypothesis held."""
    if feature not in FEATURES:
        raise ValueError(f"feature must be one of {sorted(FEATURES)}")
    if direction not in (1, -1):
        raise ValueError("direction must be 1 or -1")
    rows = con.execute(f"""
        WITH cal AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS k
            FROM (SELECT DISTINCT business_date FROM adjusted_prices)
        ), fwd AS (
            SELECT a.lineage, c.k, a.close_adj FROM adjusted_prices a JOIN cal c USING (business_date)
        ), panel AS (
            SELECT c.business_date, f.lineage, f.{feature} * {direction} AS x,
                   COALESCE(b.close_adj,
                            (SELECT arg_max(l.close_adj, l.k) FROM fwd l
                             WHERE l.lineage = f.lineage AND l.k > c.k AND l.k < c.k + ?),
                            a.close_adj) / a.close_adj - 1 AS y,
                   b.close_adj IS NULL AS filled
            FROM features f JOIN cal c USING (business_date)
            JOIN fwd a ON a.lineage = f.lineage AND a.k = c.k
            LEFT JOIN fwd b ON b.lineage = f.lineage AND b.k = c.k + ?
            WHERE (c.k - 1) % ? = 0 AND f.{feature} IS NOT NULL AND f.adv20 >= ?
              AND (? IS NULL OR c.business_date >= CAST(? AS DATE))
              AND (? IS NULL OR c.business_date <= CAST(? AS DATE))
              AND c.k + ? <= (SELECT MAX(k) FROM cal)
        ), ranked AS (
            SELECT *, RANK() OVER (PARTITION BY business_date ORDER BY x) AS rx,
                      RANK() OVER (PARTITION BY business_date ORDER BY y) AS ry,
                      NTILE(5) OVER (PARTITION BY business_date ORDER BY x) AS q
            FROM panel WHERE y IS NOT NULL
        )
        SELECT business_date, COUNT(*) AS n, CORR(rx, ry) AS ic,
               AVG(y) FILTER (WHERE q = 5) - AVG(y) FILTER (WHERE q = 1) AS spread,
               COUNT(*) FILTER (WHERE filled) AS filled,
               AVG(y) FILTER (WHERE q = 5) - AVG(y) AS top_excess,
               LIST(lineage) FILTER (WHERE q = 5) AS top
        FROM ranked r GROUP BY business_date HAVING COUNT(*) >= 20 ORDER BY 1
    """, [horizon, horizon, horizon, min_adv, start, start, end, end, horizon]).fetchall()
    ics = [r[2] for r in rows if r[2] is not None]
    spreads = [r[3] for r in rows if r[3] is not None]
    n = len(ics)
    mean = sum(ics) / n if n else float("nan")
    sd = math.sqrt(sum((x - mean) ** 2 for x in ics) / (n - 1)) if n > 1 else float("nan")
    return {
        "feature": feature, "horizon": horizon, "dates": n,
        "mean_ic": mean, "ic_t": mean / sd * math.sqrt(n) if n > 1 and sd > 0 else float("nan"),
        "hit_rate": sum(1 for x in ics if x > 0) / n if n else float("nan"),
        "mean_spread": sum(spreads) / len(spreads) if spreads else float("nan"),
        "avg_names": sum(r[1] for r in rows) / len(rows) if rows else 0,
        "filled_no_outcome": sum(r[4] for r in rows),
        "first": rows[0][0] if rows else None, "last": rows[-1][0] if rows else None,
        # Per rebalance date, for the validation firewall: rank IC, Q5-Q1 spread, the
        # long-only top quintile's excess over the universe, and its members.
        "series": [{"date": r[0], "n": r[1], "ic": r[2], "spread": r[3],
                    "top_excess": r[5], "top": r[6]} for r in rows],
    }
