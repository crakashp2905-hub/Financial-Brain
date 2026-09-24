"""A measured impact cost, replacing a table of guesses (C05 continued).

## Why this exists

``costs/india.py`` computes the statutory part of a round trip exactly - STT, exchange,
SEBI, stamp and GST are published rates and there is nothing to estimate. It then adds an
**impact** term from a hardcoded table, and that term is 71% of the total for a mid-cap.
Its own docstring calls impact "the single largest and least certain term".

That number decided fifty-six rejections. The one signal that ever cleared the
significance gate, ``above_ma200``, grosses +0.24% a period and nets anywhere from -0.26%
to +0.09% depending purely on what impact is assumed. The statistics are identical across
that whole range. So the project does not currently know whether its best signal makes
money, and the reason is an unmeasured constant.

## The methodology, fixed before any strategy is re-run

Written down first, deliberately. Revising a cost constant for a stated reason is a code
change; revising it until a strategy passes is fraud, and the only difference between them
is whether the method was chosen before or after the answer was seen.

Impact for one side of a trade is **a spread floor plus a size term**::

    impact(order) = half_spread + 0.5 * lambda * order_value

* **half_spread** - half the effective bid-ask spread, the cost of crossing once. It is a
  floor: paid on the smallest possible order, and it does not shrink with size. Estimated
  per name by **Corwin & Schultz (2012)**, which recovers the spread from daily high-low
  ratios by exploiting the fact that volatility scales with the time between observations
  while the spread does not. It needs only daily OHLC, which this archive has, and no
  quote data, which it does not.

* **lambda** - the **Amihud (2002)** illiquidity ratio, mean(|return| / rupee volume),
  read as a Kyle lambda so an order of size X moves the price by lambda * X and pays half
  of that on average. This is the part that grows with the order.

Neither estimator is exact. Corwin-Schultz is noisy for individual names and is used here
only as a bucket median. Amihud understates the marginal cost of an aggressive order,
because it divides by *total* volume including the passive side. Both errors point the
same way - understatement - which is why the floor below is applied rather than trusting
a small number.

## The floor

``MIN_HALF_SPREAD_BPS`` keeps the estimate honest at the liquid end, where both estimators
tend toward zero and reality does not: no real order crosses for free. An estimate below
the floor is replaced by it, and the number of replacements is reported rather than
hidden.
"""
from __future__ import annotations

VERSION = "cm1"
MIN_HALF_SPREAD_BPS = 2.0     # no real fill crosses for less than this
MAX_HALF_SPREAD_BPS = 300.0   # above this the estimator is fitting noise, not a spread
DEFAULT_ORDER_INR = 1_000_000

#: Corwin & Schultz (2012), equations 14-18. Two consecutive sessions give beta (the sum
#: of each session's squared log high-low range) and gamma (the squared log range over the
#: two sessions combined). Volatility scales with elapsed time and the spread does not,
#: and alpha is what separates them.
SPREAD_SQL = """
CREATE OR REPLACE TEMP TABLE _cs AS
WITH b AS (
    SELECT p.lineage, p.business_date AS d, p.turnover AS tv,
           e.high_price * p.factor AS h,
           e.low_price  * p.factor AS l
    FROM adjusted_prices p
    JOIN eod_prices e
      ON e.isin = p.isin AND e.business_date = p.business_date
     AND e.exchange = 'NSE' AND e.series = 'EQ'
    WHERE p.close_adj > 0 AND e.high_price >= e.low_price AND e.low_price > 0
      AND p.business_date >= ?
), pairs AS (
    SELECT lineage, d, tv, h, l,
           LAG(h) OVER w AS h0, LAG(l) OVER w AS l0
    FROM b WINDOW w AS (PARTITION BY lineage ORDER BY d)
), ab AS (
    SELECT lineage, d, tv,
           POW(LN(h0 / l0), 2) + POW(LN(h / l), 2)          AS beta,
           POW(LN(GREATEST(h, h0) / LEAST(l, l0)), 2)       AS gamma
    FROM pairs WHERE h0 IS NOT NULL AND l0 > 0 AND l > 0
), alpha AS (
    SELECT lineage, d, tv,
           (SQRT(2 * beta) - SQRT(beta)) / (3 - 2 * SQRT(2))
             - SQRT(gamma / (3 - 2 * SQRT(2)))              AS a
    FROM ab
)
SELECT lineage, d, tv,
       -- S = 2(e^a - 1)/(1 + e^a), with negative alphas set to ZERO, not dropped.
       --
       -- This is Corwin & Schultz's own prescription and the first version here got it
       -- backwards, on the reasoning that clamping "would drag the median down". It is
       -- the opposite: 34.2% of alphas are negative, so the estimator is mostly noise
       -- around a small true spread, and discarding the negative half then taking the
       -- median of the survivors returns roughly the 75th percentile of that noise. It
       -- produced a 53.8 bps half-spread for the most liquid 5% of Indian equities -
       -- a full spread above 1% on names like Reliance, where the real figure is a few
       -- basis points. An implausible number is what caught it.
       CASE WHEN a > 0 THEN 2 * (EXP(a) - 1) / (1 + EXP(a)) ELSE 0 END AS spread
FROM alpha;
"""

#: Amihud (2002): average absolute return per rupee of turnover.
LAMBDA_SQL = """
CREATE OR REPLACE TEMP TABLE _amihud AS
WITH r AS (
    SELECT lineage, business_date AS d, turnover AS tv,
           ABS(close_adj / NULLIF(LAG(close_adj) OVER w, 0) - 1) AS aret
    FROM adjusted_prices
    WHERE close_adj > 0 AND business_date >= ?
    WINDOW w AS (PARTITION BY lineage ORDER BY business_date)
)
SELECT lineage, d, tv, aret / tv AS lam
FROM r WHERE aret IS NOT NULL AND aret <= 0.5 AND tv > 0;
"""

#: The liquidity buckets costs/india.py names, as per-session turnover percentiles.
BUCKET_CASE = """CASE WHEN pct <= 5 THEN 'mega' WHEN pct <= 15 THEN 'large'
                      WHEN pct <= 38 THEN 'mid' WHEN pct <= 75 THEN 'small'
                      ELSE 'micro' END"""

ORDER = ("mega", "large", "mid", "small", "micro")


def estimate(con, *, since: str = "2016-01-01",
             order_inr: float = DEFAULT_ORDER_INR) -> dict:
    """Half-spread and lambda per liquidity bucket, and the impact they imply."""
    con.execute(SPREAD_SQL, [since])
    con.execute(LAMBDA_SQL, [since])
    rows = con.execute(f"""
        WITH ranked AS (
            SELECT s.lineage, s.d, s.spread, a.lam,
                   NTILE(100) OVER (PARTITION BY s.d ORDER BY s.tv DESC) AS pct
            FROM _cs s JOIN _amihud a ON a.lineage = s.lineage AND a.d = s.d
            WHERE s.tv > 0
        )
        SELECT {BUCKET_CASE} AS bucket, COUNT(*),
               -- MEAN, per Corwin & Schultz: the zeros from clamped negative alphas
               -- carry information about how small the true spread is, and a median
               -- over a distribution that is ~1/3 zeros would discard it.
               AVG(spread), MEDIAN(lam),
               COUNT(*) FILTER (WHERE spread = 0)
        FROM ranked GROUP BY 1""").fetchall()

    out, floored = {}, []
    for bucket, n, mean_spread, med_lam, n_null in rows:
        raw_half = (mean_spread or 0) / 2 * 10_000
        half_bps = min(max(raw_half, MIN_HALF_SPREAD_BPS), MAX_HALF_SPREAD_BPS)
        if raw_half < MIN_HALF_SPREAD_BPS:
            floored.append(bucket)
        size_bps = 0.5 * (med_lam or 0) * order_inr * 10_000
        out[bucket] = {"n": n, "raw_half_spread_bps": round(raw_half, 3),
                       "half_spread_bps": round(half_bps, 2),
                       "size_bps": round(size_bps, 2),
                       "impact_bps": round(half_bps + size_bps, 2),
                       "impact": round((half_bps + size_bps) / 10_000, 6),
                       "zero_spread_estimates": n_null}
    return {"version": VERSION, "order_inr": order_inr, "since": since,
            "buckets": out, "floored": floored}


def as_impact_table(con, **kw) -> dict:
    """The measured equivalent of ``costs.india.DEFAULT_IMPACT``."""
    return {b: v["impact"] for b, v in estimate(con, **kw)["buckets"].items()}


def compare(con, **kw) -> list[dict]:
    """Measured against assumed, side by side - the only honest way to change a constant."""
    from .india import DEFAULT_IMPACT
    est = estimate(con, **kw)["buckets"]
    return [{"bucket": b,
             "assumed_bps": round(DEFAULT_IMPACT[b] * 10_000, 1),
             "measured_bps": est[b]["impact_bps"],
             "half_spread_bps": est[b]["half_spread_bps"],
             "size_bps": est[b]["size_bps"],
             "ratio": round(est[b]["impact_bps"] / (DEFAULT_IMPACT[b] * 10_000), 2)}
            for b in ORDER if b in est]


def round_trip_bps(impact_one_way: float, *, stt_both_legs: float = 0.002,
                   other: float = 0.00009) -> float:
    """Round-trip friction in bps given a one-way impact. The statutory terms are exact."""
    return (2 * impact_one_way + stt_both_legs + other) * 10_000


# --- the bracket -------------------------------------------------------------
#
# Corwin-Schultz does not work on this data. It returns a 36 bps half-spread for the most
# liquid 5% of Indian equities - a 72 bps round-trip spread on names where NSE publishes
# an impact cost of 2-6 bps, and where one tick is 0.95 bps. The estimator is known to
# degrade when volatility is large relative to the spread, and Indian equities average a
# 4.2% daily range. It is kept in this module because a measurement that failed is worth
# recording, and removed from use.
#
# What remains is a bracket rather than a point:
#
#   optimistic   one tick of half-spread, plus the Amihud size term. A hard lower bound:
#                no fill crosses for less than a tick, and NSE's own published impact
#                cost for Nifty 50 sits just above this.
#   pessimistic  the original hardcoded table, which the tick floor shows to be 3-10x
#                high for liquid names but which is defensible as a worst case.
#
# The gate uses the pessimistic bound, so nothing is ever promoted on a hopeful cost
# assumption. The optimistic bound is reported alongside, which makes visible exactly
# which results are decided by cost uncertainty rather than by evidence - and that is the
# thing the single hardcoded number was hiding.

NSE_TICK_INR = 0.05

TICK_SQL = f"""
WITH r AS (
    SELECT lineage, business_date AS d, close_adj AS c,
           NTILE(100) OVER (PARTITION BY business_date ORDER BY turnover DESC) AS pct
    FROM adjusted_prices WHERE close_adj > 0 AND business_date >= ?
)
SELECT {BUCKET_CASE} AS bucket, MEDIAN({NSE_TICK_INR} / c) * 10000 AS tick_bps
FROM r GROUP BY 1
"""


def bracket(con, *, since: str = "2016-01-01",
            order_inr: float = DEFAULT_ORDER_INR) -> dict:
    """Optimistic and pessimistic one-way impact per bucket, in fractions of notional."""
    from .india import DEFAULT_IMPACT
    est = estimate(con, since=since, order_inr=order_inr)["buckets"]
    ticks = dict(con.execute(TICK_SQL, [since]).fetchall())
    out = {}
    for b in ORDER:
        if b not in est:
            continue
        optimistic_bps = ticks.get(b, MIN_HALF_SPREAD_BPS) + est[b]["size_bps"]
        pessimistic_bps = DEFAULT_IMPACT[b] * 10_000
        out[b] = {"optimistic": round(optimistic_bps / 10_000, 6),
                  "pessimistic": round(max(pessimistic_bps, optimistic_bps) / 10_000, 6),
                  "optimistic_bps": round(optimistic_bps, 2),
                  "pessimistic_bps": round(max(pessimistic_bps, optimistic_bps), 2),
                  "tick_bps": round(ticks.get(b, 0), 2),
                  "size_bps": est[b]["size_bps"]}
    return out
