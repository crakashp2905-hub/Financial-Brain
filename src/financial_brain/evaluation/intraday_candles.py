"""Candlestick patterns on minute bars, which is not the same test as on daily bars.

Daily candlestick patterns were tested here and all seven rejected - and the investigation
found they were selecting on *volatility* rather than direction, with the directionless
doji control firing at t = -13.60. That result does not transfer automatically to minute
bars, for reasons worth stating before testing rather than after.

## Why the intraday version is a different question

**The gaps are gone.** A daily candle's open is the auction price after a night of news, so
the "body" mixes an overnight jump with a session of trading. A minute candle's open is the
previous minute's close, near enough. The shapes therefore mean what the textbooks say they
mean - a long lower shadow really is intraday rejection of a price, not a gap that filled.

**The sample is 375x larger.** 18.45M minute bars against 4.86M daily ones, and every
pattern fires far more often. That cuts noise, and it removes the capacity excuse the daily
patterns had.

**The cost is 375x worse.** A pattern acted on at minute resolution pays a round trip for a
move measured in minutes. [[Intraday breakouts pay seven times their edge]] established the
scale: gross edges of 0.03-0.10% against a 0.71% round trip. Any minute-level pattern faces
the same arithmetic, and the honest expectation is that it loses by the same order.

So the interesting number here is **gross**, not net. If a pattern has no gross edge at
minute resolution, that is a statement about the pattern. If it has one and still loses,
that is a statement about Indian transaction costs, and this project has made it three
times already.

## What is measured

Each pattern is detected on a minute bar, and the forward return is measured over the next
N minutes **within the same session** - never across the close, because holding overnight
is a different strategy with different risk. Bars in the last N minutes of a session are
skipped rather than truncated: a pattern at 15:28 has nowhere to go.

Geometry is identical to ``features/candles`` so the daily and intraday results are
comparable - same thresholds, same definitions, one difference only, which is the bar
length.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

#: Same thresholds as the daily detector, deliberately. Re-tuning them for minute bars
#: would make the two results incomparable and would be the first step toward fitting.
DOJI_BODY = 0.10
LONG_BODY = 0.60
SHADOW_RATIO = 2.0
SMALL_SHADOW = 0.10
TREND_BARS = 5          # the "prior move" window, in minutes rather than sessions
TREND_MOVE = 0.002      # 0.2% over 5 minutes counts as a prior move intraday

HORIZONS = (5, 15, 30, 60)

#: Detection SQL over a minute panel. One row per bar, one column per pattern.
DETECT_SQL = f"""
CREATE OR REPLACE TEMP TABLE _mc AS
WITH b AS (
    SELECT tradingsymbol, ts, ts::DATE AS d, open AS o, high AS h, low AS l,
           close AS c,
           ROW_NUMBER() OVER (PARTITION BY tradingsymbol, ts::DATE ORDER BY ts) AS n,
           COUNT(*)    OVER (PARTITION BY tradingsymbol, ts::DATE)              AS session_bars
    FROM minute_bars
    WHERE high > low AND close > 0
), g AS (
    SELECT *,
        ABS(c - o)                      AS body,
        h - l                           AS rng,
        h - GREATEST(o, c)              AS upper,
        LEAST(o, c) - l                 AS lower,
        LAG(c) OVER w                   AS prev_c,
        LAG(o) OVER w                   AS prev_o,
        LAG(h) OVER w                   AS prev_h,
        LAG(l) OVER w                   AS prev_l,
        LAG(c, 2) OVER w                AS prev2_c,
        LAG(o, 2) OVER w                AS prev2_o,
        c / NULLIF(LAG(c, {TREND_BARS}) OVER w, 0) - 1 AS ret_prior
    FROM b
    WINDOW w AS (PARTITION BY tradingsymbol, d ORDER BY ts)
)
SELECT tradingsymbol, ts, d, n, session_bars, c,
    CASE WHEN body <= {DOJI_BODY} * rng THEN 1 ELSE 0 END AS k_doji,
    CASE WHEN body <= 0.35 * rng AND lower >= {SHADOW_RATIO} * body
              AND upper <= {SMALL_SHADOW} * rng
              AND ret_prior <= -{TREND_MOVE} THEN 1 ELSE 0 END AS k_hammer,
    CASE WHEN body <= 0.35 * rng AND upper >= {SHADOW_RATIO} * body
              AND lower <= {SMALL_SHADOW} * rng
              AND ret_prior >= {TREND_MOVE} THEN 1 ELSE 0 END AS k_shooting_star,
    CASE WHEN c > o AND prev_c < prev_o AND c >= prev_o AND o <= prev_c
              AND body >= {LONG_BODY} * rng
              AND ret_prior <= -{TREND_MOVE} THEN 1 ELSE 0 END AS k_bullish_engulfing,
    CASE WHEN c < o AND prev_c > prev_o AND c <= prev_o AND o >= prev_c
              AND body >= {LONG_BODY} * rng
              AND ret_prior >= {TREND_MOVE} THEN 1 ELSE 0 END AS k_bearish_engulfing,
    CASE WHEN prev2_c < prev2_o AND ABS(prev_c - prev_o) <= 0.3 * (prev_h - prev_l)
              AND c > o AND c > (prev2_o + prev2_c) / 2
              AND ret_prior <= -{TREND_MOVE} THEN 1 ELSE 0 END AS k_morning_star,
    CASE WHEN c > o AND body >= 0.90 * rng AND upper <= {SMALL_SHADOW} * rng
              AND lower <= {SMALL_SHADOW} * rng THEN 1 ELSE 0 END AS k_marubozu_bull
FROM g
WHERE n > {TREND_BARS};
"""

PATTERNS = ("k_doji", "k_hammer", "k_shooting_star", "k_bullish_engulfing",
            "k_bearish_engulfing", "k_morning_star", "k_marubozu_bull")


def _t(xs) -> float:
    return mean(xs) / stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and stdev(xs) > 0 else 0.0


def detect(con) -> dict:
    """Run the detector and report how often each pattern fires."""
    for statement in DETECT_SQL.strip().split(";\n"):
        if statement.strip():
            con.execute(statement)
    cols = ", ".join(f"SUM({p})" for p in PATTERNS)
    row = con.execute(f"SELECT COUNT(*), {cols} FROM _mc").fetchone()
    return {"bars": row[0],
            "fired": {p: int(v or 0) for p, v in zip(PATTERNS, row[1:], strict=True)}}


def forward_returns(con, pattern: str, horizon: int = 15) -> dict:
    """The return over the next ``horizon`` minutes, within the session only.

    Bars too close to the close are excluded rather than truncated - a pattern at 15:28
    has nowhere to go, and letting it "hold" to a session end that is two minutes away
    would report a tiny return as though it were a completed trade.
    """
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern!r}")
    rows = con.execute(f"""
        WITH f AS (
            SELECT tradingsymbol, ts, {pattern} AS fired, c,
                   LEAD(c, {horizon}) OVER (PARTITION BY tradingsymbol, d ORDER BY ts) AS c_fwd,
                   n, session_bars
            FROM _mc
        )
        SELECT fired, c_fwd / c - 1 AS ret
        FROM f
        WHERE c_fwd IS NOT NULL AND c > 0
          AND n <= session_bars - {horizon}""").fetchall()

    hit = [r[1] for r in rows if r[0] == 1 and r[1] is not None]
    base = [r[1] for r in rows if r[0] == 0 and r[1] is not None]
    if not hit:
        return {"pattern": pattern, "horizon": horizon, "n": 0}
    edge = mean(hit) - (mean(base) if base else 0.0)
    return {"pattern": pattern, "horizon": horizon, "n": len(hit),
            "mean_after": mean(hit), "mean_baseline": mean(base) if base else 0.0,
            "gross_edge": edge, "t": _t(hit), "baseline_n": len(base),
            "win_rate": sum(1 for x in hit if x > 0) / len(hit)}


def sweep(con, horizons=HORIZONS) -> list[dict]:
    """Every pattern at every horizon - the whole grid, reported together.

    Reported as one table on purpose. Twenty-eight cells will contain a largest value by
    construction, and showing only that one is how a noise maximum becomes a finding.
    """
    out = []
    for p in PATTERNS:
        for h in horizons:
            out.append(forward_returns(con, p, h))
    return out
