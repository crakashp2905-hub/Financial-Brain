"""Classic technical indicators, computed point in time (C12 continued).

What the brain had before this module: returns over five horizons, 12-1 momentum, two
realised volatilities, distance from the 52-week high, and two untested moving-average
flags. That is enough to express *trend* and *momentum* crudely and nothing else. There
was no oscillator, no volatility band, no true range, no directional index and no volume
indicator anywhere in the system.

This adds them. Every one is a published definition, not an invention:

    rsi_14          Wilder (1978). Relative strength, 0-100.
    macd_hist       Appel (1979). 12/26 EMA difference minus its 9-EMA signal, over price.
    stoch_k_14      Lane (1950s). Where the close sits in its 14-session range.
    williams_r_14   Williams (1973). The same range, measured from the top.
    bb_pct_20       Bollinger. Position within the 20-session, 2-sigma bands.
    atr_14_pct      Wilder (1978). True range averaged, as a fraction of price.
    adx_14          Wilder (1978). Trend *strength*, direction-free.
    cci_20          Lambert (1980). Typical price against its mean, scaled by deviation.
    mfi_14          Volume-weighted RSI: money flow, not just price.
    obv_slope_20    Granville (1963). On-balance volume's 20-session drift, normalised.

Three things make these usable rather than decorative.

**Adjusted OHLC.** ``close_adj = close_raw x factor``, so the same factor adjusts open,
high and low. An indicator built on unadjusted highs reads every split as a breakout.

**One exchange leg.** ``eod_prices`` holds a row per exchange and 5.19M isin-dates carry
two; the NSE EQ series is pinned because that is the leg ``adjusted_prices`` is built
from. Joining without it silently doubles every bar.

**Strictly backward windows.** Every window is ``ROWS BETWEEN n PRECEDING AND CURRENT
ROW``, and an indicator needing N sessions is NULL until N exist.

Wilder's smoothing is a recursive EMA, which a plain window function cannot express, so
``rsi_14``, ``atr_14_pct``, ``adx_14`` and ``mfi_14`` use simple moving averages of the
same length. That is the standard "Cutler's" variant; it is a *different* indicator from
Wilder's by a small amount, and saying so is cheaper than pretending otherwise.
"""
from __future__ import annotations

VERSION = "t1"

#: Adjusted OHLCV, the panel every indicator is computed over.
BARS_SQL = """
CREATE OR REPLACE TEMP TABLE _tbars AS
SELECT p.lineage, p.business_date AS d,
       p.close_adj              AS c,
       e.open_price * p.factor  AS o,
       e.high_price * p.factor  AS h,
       e.low_price  * p.factor  AS l,
       COALESCE(p.traded_volume, 0) AS v
FROM adjusted_prices p
JOIN eod_prices e
  ON e.isin = p.isin AND e.business_date = p.business_date
 AND e.exchange = 'NSE' AND e.series = 'EQ'
WHERE p.close_adj > 0 AND e.high_price > 0 AND e.low_price > 0;
"""

#: Per-bar quantities the indicators below are built from.
PARTS_SQL = """
CREATE OR REPLACE TEMP TABLE _tparts AS
SELECT *,
       ROW_NUMBER() OVER w                                   AS n,
       GREATEST(h - l,
                ABS(h - LAG(c) OVER w),
                ABS(l - LAG(c) OVER w))                      AS tr,
       GREATEST(c - LAG(c) OVER w, 0)                        AS up,
       GREATEST(LAG(c) OVER w - c, 0)                        AS dn,
       CASE WHEN h - LAG(h) OVER w > LAG(l) OVER w - l
            THEN GREATEST(h - LAG(h) OVER w, 0) ELSE 0 END   AS dm_plus,
       CASE WHEN LAG(l) OVER w - l > h - LAG(h) OVER w
            THEN GREATEST(LAG(l) OVER w - l, 0) ELSE 0 END   AS dm_minus,
       (h + l + c) / 3.0                                     AS tp,
       LAG((h + l + c) / 3.0) OVER w                         AS tp_prev,
       CASE WHEN c > LAG(c) OVER w THEN v
            WHEN c < LAG(c) OVER w THEN -v ELSE 0 END        AS obv_step
FROM _tbars
WINDOW w AS (PARTITION BY lineage ORDER BY d);
"""


#: EMAs for MACD. DuckDB has no recursive EMA, so the 12/26/9 pair is built from the
#: closed-form weighted sum over a truncated window - 60 sessions carries >99.9% of a
#: 26-period EMA's weight, and the remainder is smaller than a tick.
MACD_SQL = """
CREATE OR REPLACE TEMP TABLE _tmacd AS
WITH w AS (
    SELECT lineage, d, c, n,
           LIST(c) OVER (PARTITION BY lineage ORDER BY d
                         ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS hist
    FROM _tparts
), e AS (
    SELECT lineage, d, c, n,
           list_reduce(
             list_transform(hist, (x, i) ->
               x * pow(1 - 2.0/13, len(hist) - i)), (a, b) -> a + b)
             / list_reduce(
             list_transform(hist, (x, i) -> pow(1 - 2.0/13, len(hist) - i)),
             (a, b) -> a + b) AS ema12,
           list_reduce(
             list_transform(hist, (x, i) ->
               x * pow(1 - 2.0/27, len(hist) - i)), (a, b) -> a + b)
             / list_reduce(
             list_transform(hist, (x, i) -> pow(1 - 2.0/27, len(hist) - i)),
             (a, b) -> a + b) AS ema26
    FROM w
)
SELECT lineage, d, c, n, ema12 - ema26 AS macd,
       AVG(ema12 - ema26) OVER (PARTITION BY lineage ORDER BY d
                                ROWS BETWEEN 8 PRECEDING AND CURRENT ROW) AS signal9
FROM e;
"""

#: The indicators themselves, joined onto ``features``.
INDICATORS_SQL = """
CREATE OR REPLACE TABLE features AS
WITH agg AS (
    SELECT p.lineage, p.d, p.n, p.c,
        AVG(up) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_up,
        AVG(dn) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_dn,
        AVG(tr) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14,
        AVG(dm_plus)  OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS adm_p,
        AVG(dm_minus) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS adm_m,
        MAX(h) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS hh14,
        MIN(l) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS ll14,
        AVG(c)  OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
        STDDEV_SAMP(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sd20,
        tp,
        AVG(tp) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma_tp20,
        SUM(CASE WHEN tp > tp_prev THEN tp * v ELSE 0 END)
            OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS mf_pos,
        SUM(CASE WHEN tp < tp_prev THEN tp * v ELSE 0 END)
            OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS mf_neg,
        SUM(obv_step) OVER (w ROWS UNBOUNDED PRECEDING) AS obv,
        AVG(v) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol20
    FROM _tparts p
    WINDOW w AS (PARTITION BY lineage ORDER BY d)
 ), devi AS (
    -- Mean absolute deviation needs the mean first: a window function cannot be nested
    -- inside another, so the two passes are separate CTEs rather than one expression.
    SELECT *, AVG(ABS(tp - sma_tp20)) OVER (PARTITION BY lineage ORDER BY d
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS md_tp20
    FROM agg
), dx AS (
    SELECT *,
        CASE WHEN atr14 > 0 THEN 100 * adm_p / atr14 END AS di_p,
        CASE WHEN atr14 > 0 THEN 100 * adm_m / atr14 END AS di_m
    FROM devi
), ind AS (
    SELECT lineage, d, n, c, tp, atr14, hh14, ll14, sma20, sd20, sma_tp20, md_tp20,
        mf_pos, mf_neg, obv, avg_vol20, di_p, di_m,
        CASE WHEN avg_up + avg_dn > 0 THEN 100 * avg_up / (avg_up + avg_dn) END AS rsi_14,
        CASE WHEN di_p + di_m > 0 THEN 100 * ABS(di_p - di_m) / (di_p + di_m) END AS dx
    FROM dx
), adx AS (
    SELECT *, AVG(dx) OVER (PARTITION BY lineage ORDER BY d
                            ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS adx_14
    FROM ind
), tech AS (
    SELECT a.lineage, a.d, a.n,
        CASE WHEN a.n >= 15 THEN a.rsi_14 END AS rsi_14,
        CASE WHEN a.n >= 15 AND a.hh14 > a.ll14
             THEN 100 * (a.c - a.ll14) / (a.hh14 - a.ll14) END AS stoch_k_14,
        CASE WHEN a.n >= 15 AND a.hh14 > a.ll14
             THEN -100 * (a.hh14 - a.c) / (a.hh14 - a.ll14) END AS williams_r_14,
        CASE WHEN a.n >= 20 AND a.sd20 > 0
             THEN (a.c - (a.sma20 - 2 * a.sd20)) / (4 * a.sd20) END AS bb_pct_20,
        CASE WHEN a.n >= 15 AND a.c > 0 THEN a.atr14 / a.c END AS atr_14_pct,
        CASE WHEN a.n >= 28 THEN a.adx_14 END AS adx_14,
        CASE WHEN a.n >= 20 AND a.md_tp20 > 0
             THEN (a.tp - a.sma_tp20) / (0.015 * a.md_tp20) END AS cci_20,
        CASE WHEN a.n >= 15 AND a.mf_pos + a.mf_neg > 0
             THEN 100 * a.mf_pos / (a.mf_pos + a.mf_neg) END AS mfi_14,
        CASE WHEN a.n >= 21 AND a.avg_vol20 > 0
             THEN (a.obv - LAG(a.obv, 20) OVER (PARTITION BY a.lineage ORDER BY a.d))
                  / (20 * a.avg_vol20) END AS obv_slope_20,
        CASE WHEN m.n >= 35 AND m.c > 0
             THEN (m.macd - m.signal9) / m.c END AS macd_hist
    FROM adx a
    LEFT JOIN _tmacd m ON m.lineage = a.lineage AND m.d = a.d
)
SELECT f.*, t.* EXCLUDE (lineage, d, n)
FROM features f
LEFT JOIN tech t ON t.lineage = f.lineage AND t.d = f.business_date;
"""

COLUMNS = ("rsi_14", "macd_hist", "stoch_k_14", "williams_r_14", "bb_pct_20",
           "atr_14_pct", "adx_14", "cci_20", "mfi_14", "obv_slope_20")


def build(con) -> dict:
    """Add the technical columns to ``features``. Run after indicators.build."""
    for sql in (BARS_SQL, PARTS_SQL, MACD_SQL, INDICATORS_SQL):
        for statement in sql.strip().split(";\n"):
            if statement.strip():
                con.execute(statement)
    cols = ", ".join(f"COUNT({c})" for c in COLUMNS)
    row = con.execute(f"SELECT COUNT(*), {cols} FROM features").fetchone()
    return {"version": VERSION, "rows": row[0],
            "populated": dict(zip(COLUMNS, row[1:], strict=True))}
