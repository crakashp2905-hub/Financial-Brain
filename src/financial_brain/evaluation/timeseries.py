"""Time-series strategies: entry and exit rules, not a cross-sectional rank.

Everything measured in this project so far has been a **factor**: score every name, rank
them, hold the top fifth, rebalance. That shape covers momentum, value, news flow and the
rest, and ``firewall.validate`` is built around it.

It does not cover the other large family of published strategies - Donchian breakouts,
moving-average crossovers, Bollinger reversion, Faber's trend filter - which are
**stateful**: a name is bought when a condition fires and held until a *different*
condition fires, with no ranking anywhere. A quintile test cannot express "in since the
20-day high broke, out when the 10-day low breaks", so until now those strategies could
not be evaluated here at all. That was an architectural gap, not a judgement about them.

This module closes it, and hands the result to the same gates and the same trial ledger.

## How a strategy is written

A strategy is two SQL predicates over a windowed OHLC panel - when to enter, when to exit -
and the position between them is carried by the standard state-machine idiom:

    last_value(CASE WHEN entry THEN 1 WHEN exit THEN 0 END IGNORE NULLS)
        OVER (PARTITION BY lineage ORDER BY d ROWS UNBOUNDED PRECEDING)

Stateless strategies (a crossover is simply "fast above slow") set ``entry`` and ``exit``
to complementary conditions and get the same machinery for free.

## Three decisions that make the numbers mean something

**Execution lags a day.** A signal computed from a session's close cannot be traded at
that close. The position formed on day *t* earns the return from *t+1* to *t+2*. The
alternative convention - entering at the close that produced the signal - is common in
published backtests and quietly buys a day of hindsight on every trade.

**Prices are adjusted, including the highs and lows.** ``close_adj = close_raw x factor``,
so the same factor adjusts open, high and low. Running a breakout on unadjusted highs
manufactures a signal out of every split and bonus issue.

**The benchmark is the equal-weighted universe, and cash counts against it.** A strategy
flat during a rally has earned nothing while the benchmark earned something, and that
shows up as negative excess. Measuring a timing system against its own invested periods
only is how a strategy that is out of the market for most of a decade looks good. See
[[Beating the median is not an edge]].
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import NormalDist, mean, stdev

from ..costs import book
from ..costs.book import BUCKETS
from ..costs.india import CostModel
from ..features import candles
from .firewall import (ALPHA, MIN_DSR, MIN_YEAR_AGREEMENT, REGIME_T, deflated_sharpe)

VERSION = "ts1"
N = NormalDist()
MIN_NAMES = 30              # names held on an average day; below this it is a few bets
MIN_TURNOVER = 1e7          # Rs 1cr traded, the firewall's liquidity floor
EXECUTION_LAG = 1           # sessions between the signal and the fill
MAX_ABS_RETURN = 0.50       # beyond this it is an unadjusted corporate action, not a move

#: The panel every strategy sees: corporate-action adjusted OHLC, liquid names only.
#: eod_prices carries one row per exchange per session, so the NSE EQ series is pinned -
#: it is the leg `adjusted_prices` itself is built from, and joining without that filter
#: silently doubles every bar.
PANEL = """
CREATE OR REPLACE TEMP TABLE _bars AS
SELECT p.lineage, p.business_date AS d,
       p.close_adj                AS c,
       e.high_price  * p.factor   AS h,
       e.low_price   * p.factor   AS l,
       e.open_price  * p.factor   AS o,
       p.turnover                 AS tv,
       -- Log returns at two horizons, so a variance ratio can be a window expression.
       -- VAR_SAMP over a LAG would nest one window function inside another.
       LN(p.close_adj / NULLIF(LAG(p.close_adj) OVER wl, 0))    AS r1,
       LN(p.close_adj / NULLIF(LAG(p.close_adj, 5) OVER wl, 0)) AS r5,
       -- True range, precomputed for the same reason: AVG over a LAG would nest.
       GREATEST(e.high_price * p.factor - e.low_price * p.factor,
                ABS(e.high_price * p.factor - LAG(p.close_adj) OVER wl),
                ABS(e.low_price  * p.factor - LAG(p.close_adj) OVER wl)) AS tr,
       COALESCE(p.traded_volume, 0)                             AS vol,
       -- Chaikin money-flow volume: volume signed by where the close sat in its bar.
       CASE WHEN e.high_price > e.low_price
            THEN COALESCE(p.traded_volume, 0)
                 * ((p.close_adj - e.low_price * p.factor)
                    - (e.high_price * p.factor - p.close_adj))
                 / NULLIF(e.high_price * p.factor - e.low_price * p.factor, 0)
            ELSE 0 END                                          AS mfv
FROM adjusted_prices p
JOIN eod_prices e
  ON e.isin = p.isin AND e.business_date = p.business_date
 AND e.exchange = 'NSE' AND e.series = 'EQ'
WHERE p.close_adj > 0 AND e.high_price > 0 AND e.low_price > 0
  AND p.business_date BETWEEN ? AND ?
WINDOW wl AS (PARTITION BY p.lineage ORDER BY p.business_date);
"""


#: Strategies drawn from letianzj/QuantResearch (MIT) and the sources it implements.
#:
#: Of that repository's fourteen backtest files, five are testable on what this project
#: holds. The rest are excluded on a data fact rather than an opinion:
#:
#:   dual_thrust, r_breaker, ghost_trader, dynamic_breakout_ii
#:       intraday systems keyed off the session's opening range - they need minute bars,
#:       and this archive is daily.
#:   comdty_roll, comdty_spread_roll
#:       commodity futures roll yield; there is no futures curve here.
#:   portfolio_optimization
#:       an allocator, not a signal - it needs a return forecast to allocate over, which
#:       is the thing this project does not yet have.
#:   buy_hold
#:       already the control, and already the benchmark every number below is net of.
#:
#: Each entry is (entry predicate, exit predicate, what it claims, where it comes from).
STRATEGIES: dict[str, dict] = {
    "ma_cross_200": {
        "entry": "c > sma_200", "exit": "c <= sma_200",
        "needs": ["sma_200"],
        "claim": "Hold a name while it trades above its 200-session average.",
        "source": "ma_cross.py; the oldest published trend filter there is.",
    },
    "ma_double_cross_50_200": {
        "entry": "sma_50 > sma_200", "exit": "sma_50 <= sma_200",
        "needs": ["sma_50", "sma_200"],
        "claim": "The golden cross: hold while the 50-session average is above the 200.",
        "source": "ma_double_cross.py.",
    },
    "faber_taa_10m": {
        "entry": "c > sma_200 AND is_month_end", "exit": "c <= sma_200 AND is_month_end",
        "needs": ["sma_200", "is_month_end"],
        "claim": "Faber's timing model: the same 10-month filter, checked monthly.",
        "source": "mebane_faber_taa.py; Faber (2007), 'A Quantitative Approach to "
                  "Tactical Asset Allocation'. Deliberately overlaps ma_cross_200 - the "
                  "difference is turnover, and turnover is what killed three earlier "
                  "candidates, so it is worth separating.",
    },
    "bollinger_reversion_20_2": {
        "entry": "c < sma_20 - 2 * sd_20", "exit": "c >= sma_20",
        "needs": ["sma_20", "sd_20"],
        "claim": "Buy two standard deviations below the 20-session mean, exit at the mean.",
        "source": "bollinger_bands.py, long-only leg. Shorting cash equity is not "
                  "available in India, so the short leg is dropped rather than assumed.",
    },
    "above_ma200_band": {
        "entry": "c > sma_200 * 1.05", "exit": "c < sma_200 * 0.95",
        "needs": ["sma_200"],
        "claim": "The 200-day filter with a 5% hysteresis band: enter 5% above the "
                 "average, leave only 5% below it.",
        "source": "h12. Not from the QuantResearch list - it is `above_ma200`, the only "
                  "signal to clear the Bonferroni gate, with the one variable that was "
                  "never varied. A raw close-vs-average rule flips every time price "
                  "grazes the line, which is why it turns over 70% a period; a band is "
                  "the standard, least inventive way to suppress that. b = 0.05 chosen "
                  "once as a round number outside daily noise on a 4.2% ATR, not "
                  "selected by trying several.",
    },
    "above_ma200_atr_band": {
        "entry": "c > sma_200 + 1.5 * atr_14", "exit": "c < sma_200 - 1.5 * atr_14",
        "needs": ["sma_200", "atr_14"],
        "claim": "The 200-day filter with a band scaled to each name's own volatility: "
                 "enter 1.5 ATR above the average, leave 1.5 ATR below it.",
        "source": "h13. h12 used a fixed 5% band on a universe whose 14-day ATR runs "
                  "from 2.3% to 6.4% between the 10th and 90th percentiles - so 5% was "
                  "a 2.2 ATR band on a quiet name and a 0.8 ATR band on a volatile one, "
                  "which is not one rule. This is one rule: a single universe-wide "
                  "coefficient of 1.5, with the band differing per stock because the "
                  "ATR does. Nothing is fitted per name.",
    },
    "trend_only_when_trending": {
        "entry": "c > sma_200 + 1.5 * atr_14 AND vr_60 > 1",
        "exit": "c < sma_200 - 1.5 * atr_14 OR vr_60 <= 1",
        "needs": ["sma_200", "atr_14", "vr_60"],
        "claim": "The volatility-scaled trend rule, applied only while the name's own "
                 "variance ratio says its moves persist rather than reverse.",
        "source": "h13. A trend rule on a mean-reverting name is fighting that name's "
                  "behaviour. The variance ratio measures which it is doing, from prices "
                  "alone, on a trailing 60 sessions - see features/behaviour.py.",
    },
    "trend_calm_vix": {
        "entry": "c > sma_200 + 1.5 * atr_14 AND vix_pct < 0.8",
        "exit": "c < sma_200 - 1.5 * atr_14 OR vix_pct >= 0.8",
        "needs": ["sma_200", "atr_14", "vix_pct"],
        "claim": "The volatility-scaled trend rule, stood down when India VIX is in the "
                 "top fifth of its trailing year.",
        "source": "h14. The ATR-banded rule's excess reversed significantly in RISK_OFF "
                  "(t = -2.3), and India VIX is the market's own forward-looking measure "
                  "of exactly that - present in index_levels since 2015 and never used. "
                  "The 0.8 threshold is the quintile boundary the firewall already uses "
                  "everywhere else, not a number chosen by trying several.",
    },
    "supertrend": {
        "entry": "c > st_upper", "exit": "c < st_lower",
        "needs": ["st_upper", "st_lower"],
        "claim": "Hold while the close is above a 3-ATR band around the 10-session "
                 "median price.",
        "source": "h17. Olivier Seban; the most-used single indicator on TradingView. "
                  "SIMPLIFIED: the classic form locks its band to the previous bar, which "
                  "is recursive and inexpressible as a window function. This is the entry "
                  "condition without the trailing memory - a slightly different indicator.",
    },
    "ichimoku_cloud": {
        "entry": "c > GREATEST((tenkan + kijun) / 2, senkou_b)",
        "exit": "c < LEAST((tenkan + kijun) / 2, senkou_b)",
        "needs": ["tenkan", "kijun", "senkou_b"],
        "claim": "Hold while the close is above the Ichimoku cloud.",
        "source": "h17. Goichi Hosoda, 1960s. The cloud is normally displaced 26 sessions "
                  "forward; here it is compared at the current bar, because displacing it "
                  "forward would compare today's price to a line drawn from data it has "
                  "not seen - the displacement is a drawing convention, not a signal one.",
    },
    "keltner_breakout": {
        "entry": "c > kelt_upper", "exit": "c < kelt_lower",
        "needs": ["kelt_upper", "kelt_lower"],
        "claim": "Close above a 2-ATR Keltner band on the 20-session average.",
        "source": "h17. Chester Keltner (1960), modern ATR form.",
    },
    "cmf_positive": {
        "entry": "cmf_20 > 0.05", "exit": "cmf_20 < -0.05",
        "needs": ["cmf_20"],
        "claim": "Hold while Chaikin money flow shows accumulation.",
        "source": "h17. Marc Chaikin. The +/-0.05 band is the conventional threshold, "
                  "not one chosen by trying values.",
    },
    "awesome_oscillator": {
        "entry": "ao > 0", "exit": "ao <= 0",
        "needs": ["ao"],
        "claim": "Hold while SMA(5) of the median price exceeds SMA(34).",
        "source": "h17. Bill Williams. Zero is the indicator own neutral point.",
    },
    "pivot_breakout": {
        "entry": "c > pivot_r1", "exit": "c < pivot_s1",
        "needs": ["pivot_r1", "pivot_s1"],
        "claim": "Close above the first resistance pivot of the previous session.",
        "source": "h17. Floor-trader pivots. Computed strictly from the prior session.",
    },
    "donchian_55_20": {
        "entry": "c > don_hi_55", "exit": "c < don_lo_20",
        "needs": ["don_hi_55", "don_lo_20"],
        "claim": "The Turtles slower channel: 55-session entry, 20-session exit.",
        "source": "h17. turtle_20_10 tested the fast channel; this is the other half of "
                  "the original system and was never run.",
    },
    "turtle_20_10": {
        "entry": "c > don_hi_20", "exit": "c < don_lo_10",
        "needs": ["don_hi_20", "don_lo_10"],
        "claim": "Donchian breakout: in on a 20-session high, out on a 10-session low.",
        "source": "turtle.py; the Dennis and Eckhardt system, long-only leg.",
    },
}

#: Window expressions the predicates above may reference. Every one is strictly backward
#: looking and excludes the current bar where a comparison against it would otherwise be
#: circular - a 20-day high that includes today is broken by definition.
INDICATORS = {
    "sma_20": "AVG(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
    "sma_50": "AVG(c) OVER (w ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)",
    "sma_200": "AVG(c) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)",
    "sd_20": "STDDEV_SAMP(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)",
    # Average true range in price units, so a band can be expressed in the name's own
    # volatility rather than in percent of a price that means nothing across stocks.
    "atr_14": "AVG(tr) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)",
    # Lo & MacKinlay (1988) variance ratio: Var(5-session) / (5 x Var(1-session)). Above
    # one the name's moves persist, below one they reverse. Trailing 60 sessions.
    # --- the PineScript canon (h17). Every one a published, fixed definition.
    # Supertrend / Keltner: an ATR band around a centre line. Note this is Supertrend's
    # entry condition WITHOUT its trailing lock - the classic form is recursive and no
    # window function can express it. A different indicator, slightly, and said so.
    "st_upper": "(AVG((h + l) / 2) OVER (w ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)) "
                "+ 3 * AVG(tr) OVER (w ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)",
    "st_lower": "(AVG((h + l) / 2) OVER (w ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)) "
                "- 3 * AVG(tr) OVER (w ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)",
    "kelt_upper": "AVG(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) "
                  "+ 2 * AVG(tr) OVER (w ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)",
    "kelt_lower": "AVG(c) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) "
                  "- 2 * AVG(tr) OVER (w ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)",
    # Ichimoku: Tenkan (9) and Kijun (26) are midpoints of their high-low range; the
    # cloud is the span between their average and the 52-period midpoint.
    "tenkan": "(MAX(h) OVER (w ROWS BETWEEN 8 PRECEDING AND CURRENT ROW) "
              "+ MIN(l) OVER (w ROWS BETWEEN 8 PRECEDING AND CURRENT ROW)) / 2",
    "kijun": "(MAX(h) OVER (w ROWS BETWEEN 25 PRECEDING AND CURRENT ROW) "
             "+ MIN(l) OVER (w ROWS BETWEEN 25 PRECEDING AND CURRENT ROW)) / 2",
    "senkou_b": "(MAX(h) OVER (w ROWS BETWEEN 51 PRECEDING AND CURRENT ROW) "
                "+ MIN(l) OVER (w ROWS BETWEEN 51 PRECEDING AND CURRENT ROW)) / 2",
    # Chaikin money flow: volume weighted by where the close sat in its own bar.
    "cmf_20": "SUM(mfv) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) "
              "/ NULLIF(SUM(vol) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 0)",
    # Awesome oscillator: SMA(5) - SMA(34) of the median price.
    "ao": "AVG((h + l) / 2) OVER (w ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) "
          "- AVG((h + l) / 2) OVER (w ROWS BETWEEN 33 PRECEDING AND CURRENT ROW)",
    # Floor-trader pivots from the PREVIOUS session - never this one.
    "pivot_r1": "2 * ((LAG(h) OVER w + LAG(l) OVER w + LAG(c) OVER w) / 3) "
                "- LAG(l) OVER w",
    "pivot_s1": "2 * ((LAG(h) OVER w + LAG(l) OVER w + LAG(c) OVER w) / 3) "
                "- LAG(h) OVER w",
    "don_hi_55": "MAX(h) OVER (w ROWS BETWEEN 55 PRECEDING AND 1 PRECEDING)",
    "don_lo_20": "MIN(l) OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)",
    "vr_60": "VAR_SAMP(r5) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) "
             "/ NULLIF(5 * VAR_SAMP(r1) OVER (w ROWS BETWEEN 59 PRECEDING "
             "AND CURRENT ROW), 0)",
    # Market-wide, identical for every name on a session: joined, not windowed.
    "vix": "MAX(vix) OVER (w ROWS BETWEEN CURRENT ROW AND CURRENT ROW)",
    "vix_pct": "MAX(vix_pct) OVER (w ROWS BETWEEN CURRENT ROW AND CURRENT ROW)",
    "don_hi_20": "MAX(h) OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)",
    "don_lo_10": "MIN(l) OVER (w ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)",
    "is_month_end": "d = LAST_VALUE(d) OVER (PARTITION BY lineage, "
                    "date_trunc('month', d) ORDER BY d "
                    "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)",
}


def _needed(spec: dict) -> str:
    return ", ".join(f"{INDICATORS[k]} AS {k}" for k in spec["needs"])


#: India VIX, joined by session so a rule can condition on market-wide fear rather than
#: on the name alone. It sits in `index_levels` from 2015 and had never been used as a
#: feature. Two forms, both strictly backward-looking:
#:
#:   vix          the level
#:   vix_pct      its percentile over the trailing 250 sessions, so "high" means high
#:                against the last year rather than against an absolute number that
#:                drifts with the decade
#:
#: LEFT JOIN with a COALESCE, because a missing VIX session must not silently drop every
#: stock from the panel that day.
VIX_JOIN = """
CREATE OR REPLACE TEMP TABLE _vix AS
WITH v AS (
    SELECT business_date AS d, close_level AS vix
    FROM index_levels WHERE index_name = 'India VIX' AND close_level > 0
)
SELECT d, vix,
       -- Rank against the trailing year only. A PERCENT_RANK over the whole history
       -- would use future sessions to judge today, which is the leak this project has
       -- already been bitten by twice.
       (SELECT COUNT(*) FROM v v2 WHERE v2.d <= v.d AND v2.d > v.d - INTERVAL 250 DAY
        AND v2.vix <= v.vix)::DOUBLE
       / NULLIF((SELECT COUNT(*) FROM v v3 WHERE v3.d <= v.d
                 AND v3.d > v.d - INTERVAL 250 DAY), 0) AS vix_pct
FROM v;

CREATE OR REPLACE TEMP TABLE _bars AS
SELECT b.*, COALESCE(x.vix, 0) AS vix, COALESCE(x.vix_pct, 0.5) AS vix_pct
FROM _bars b LEFT JOIN _vix x ON x.d = b.d
"""


#: Candlestick flags ride along so a pattern can be an entry rule. Applied only when the
#: table exists: a database with no candles built still runs every price-based strategy,
#: rather than failing on a join it does not need.
CANDLE_JOIN = """
CREATE OR REPLACE TEMP TABLE _bars AS
SELECT b.*, k.* EXCLUDE (lineage, business_date, tv)
FROM _bars b
LEFT JOIN candles k ON k.lineage = b.lineage AND k.business_date = b.d
"""


#: Event flags ride along the same way candles do: LEFT JOIN, because a session with no
#: filing is a zero rather than a missing row.
EVENT_JOIN = """
CREATE OR REPLACE TEMP TABLE _bars AS
SELECT b.*, e.* EXCLUDE (business_date, lineage)
FROM _bars b
LEFT JOIN event_flags e ON e.lineage = b.lineage AND e.business_date = b.d
"""


def _has_table(con, name: str) -> bool:
    return bool(con.execute("""SELECT COUNT(*) FROM duckdb_tables()
                               WHERE table_name = ?""", [name]).fetchone()[0])


def _has_candles(con) -> bool:
    return _has_table(con, "candles")


def series(con, name: str, *, start=None, end=None, min_turnover: float = MIN_TURNOVER,
           lag: int = EXECUTION_LAG, max_abs: float = MAX_ABS_RETURN) -> list[dict]:
    """One row per session: the strategy's return, the universe's, and the turnover.

    The position formed on a bar is lagged ``lag`` sessions before it earns anything, so
    a signal computed from a close is never filled at that close.
    """
    spec = STRATEGIES[name]
    con.execute(PANEL, [start or "1900-01-01", end or "2999-12-31"])
    needs_vix = any(k.startswith("vix") for k in spec["needs"])
    if needs_vix and not _has_table(con, "index_levels"):
        raise ValueError(f"{name} needs India VIX from index_levels, which is not present; "
                         "run the index ingest first")
    if needs_vix:
        for st in [q for q in VIX_JOIN.strip().split(";\n") if q.strip()]:
            con.execute(st)
    needs_events = any(k.startswith("held_") for k in spec["needs"])
    if needs_events and not _has_table(con, "event_flags"):
        raise ValueError(f"{name} needs event flags; run `fb features` to build them")
    if needs_events:
        con.execute(EVENT_JOIN)
    if _has_candles(con):
        con.execute(CANDLE_JOIN)
    elif any(k.startswith("fired_20_") for k in spec["needs"]):
        raise ValueError(f"{name} needs candlestick flags, and no `candles` table exists; "
                         "run `fb features` to build them")
    book.ensure_view(con)
    rows = con.execute(f"""
        WITH ind AS (
            SELECT lineage, d, c, tv, {_needed(spec)}
            FROM _bars
            WINDOW w AS (PARTITION BY lineage ORDER BY d)
        ), flagged AS (
            SELECT *, CASE WHEN {spec['entry']} THEN 1
                           WHEN {spec['exit']}  THEN 0 END AS flag
            FROM ind
        ), held AS (
            SELECT *, COALESCE(last_value(flag IGNORE NULLS) OVER (
                          PARTITION BY lineage ORDER BY d
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0) AS pos
            FROM flagged
        ), lagged AS (
            SELECT lineage, d, c,
                   LAG(pos, {lag}) OVER (PARTITION BY lineage ORDER BY d) AS pos,
                   LAG(pos, {lag} + 1) OVER (PARTITION BY lineage ORDER BY d) AS prev_pos,
                   -- Liquidity is judged on turnover already observed when the position
                   -- was formed. Filtering on the *current* bar's turnover selects the
                   -- sessions a name moved hard, because volume spikes with price - it
                   -- inflated this universe's return from +19% to +84% a year.
                   LAG(tv, {lag} + 1) OVER (PARTITION BY lineage ORDER BY d) AS tv_known,
                   c / NULLIF(LAG(c) OVER (PARTITION BY lineage ORDER BY d), 0) - 1 AS ret
            FROM held
        ), eligible AS (
            SELECT * FROM lagged
            WHERE ret IS NOT NULL AND pos IS NOT NULL AND prev_pos IS NOT NULL
              AND tv_known >= {min_turnover}
              -- Indian equities trade under circuit limits of 5-20% a session, so a move
              -- beyond 50% is a split or bonus this archive failed to adjust, not a market
              -- move. One of them corrupts an equal-weighted average; the raw panel holds
              -- a +3750% bar and 146 sessions above +100%.
              AND ABS(ret) <= {max_abs}
        ), bucketed AS (
            -- Impact is priced off an **absolute national** turnover rank (top 100 is
            -- mega, and so on), so the ranking population has to be the exchange's whole
            -- equity list. Ranking inside this filtered universe instead put 62% of its
            -- names inside rank 750 and charged them mid impact or better, when nationally
            -- most of them are small - an error entirely in the strategy's favour.
            SELECT e.*, COALESCE(b.bucket, 'micro') AS bucket
            FROM eligible e LEFT JOIN _liquidity_buckets b USING (d, lineage)
        )
        SELECT d,
               AVG(ret) FILTER (WHERE pos = 1)                  AS strat_gross,
               AVG(ret)                                          AS universe,
               COUNT(*) FILTER (WHERE pos = 1)                   AS held,
               COUNT(*)                                          AS eligible,
               COUNT(*) FILTER (WHERE pos = 1 AND prev_pos = 0)   AS entries,
               COUNT(*) FILTER (WHERE pos = 1 AND bucket = 'mega')  AS h_mega,
               COUNT(*) FILTER (WHERE pos = 1 AND bucket = 'large') AS h_large,
               COUNT(*) FILTER (WHERE pos = 1 AND bucket = 'mid')   AS h_mid,
               COUNT(*) FILTER (WHERE pos = 1 AND bucket = 'small') AS h_small,
               COUNT(*) FILTER (WHERE pos = 1 AND bucket = 'micro') AS h_micro
        FROM bucketed
        GROUP BY d ORDER BY d""").fetchall()
    return [{"date": r[0], "gross": r[1], "universe": r[2], "held": r[3],
             "eligible": r[4], "entries": r[5],
             "mix": dict(zip(BUCKETS, r[6:11]))} for r in rows]


def run(con, name: str, *, bucket: str = "mid", **kw) -> dict:
    """The strategy's daily excess over the equal-weighted universe, net of costs.

    A session with no position earns cash - zero - and the universe's return that day
    still counts against it. That is the honest treatment of a timing system: being out
    of a rising market is a cost, not an absence of one.
    """
    rows = series(con, name, **kw)
    if not rows:
        return {"strategy": name, "days": 0, "excess": []}

    model = CostModel()
    rt = {b: model.round_trip(turnover=1_000_000, bucket=b)["bps"] / 10_000
          for b in BUCKETS}
    flat = rt[bucket]
    excess, turns, costs, mix = [], [], [], dict.fromkeys(BUCKETS, 0)
    for r in rows:
        gross = r["gross"] if r["held"] else 0.0        # flat means cash, not absent
        # The book pays what it holds, not what the median listed company would pay. A
        # trend rule's holdings run to the illiquid end of the universe - so the flat
        # ``mid`` charge understated its round trip, in the strategy's favour.
        n = sum(r["mix"].values())
        cost = (sum(rt[b] * k for b, k in r["mix"].items()) / n) if n else flat
        for b, k in r["mix"].items():
            mix[b] += k
        costs.append(cost)
        # The firewall's convention, matched deliberately: turnover is the fraction of
        # the book *replaced*, and each replacement costs one round trip. Counting
        # entries and exits both would charge two round trips for one change of hands.
        denom = max(r["held"], 1)
        turn = r["entries"] / denom
        turns.append(min(turn, 2.0))
        excess.append(gross - (r["universe"] or 0.0) - min(turn, 2.0) * cost)
    total = sum(mix.values()) or 1
    return {"strategy": name, "days": len(rows), "rows": rows, "excess": excess,
            "turnover": mean(turns), "cost_round_trip": mean(costs),
            "cost_round_trip_flat": flat,
            "bucket_mix": {b: k / total for b, k in mix.items()},
            "avg_held": mean(r["held"] for r in rows),
            "invested_days": sum(1 for r in rows if r["held"]) / len(rows)}


def _t(xs) -> float:
    return mean(xs) / stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and stdev(xs) > 0 else 0.0


def validate(con, name: str, *, record: bool = True, **kw) -> dict:
    """The same gates the cross-sectional firewall applies, and the same trial ledger.

    Sharing the ledger is the point. A time-series strategy tested here is one more trial
    against every factor ever tested, and a factor tested there is one more trial against
    these. Two separate counters would let the same search be run twice and reported as
    two independent discoveries.
    """
    r = run(con, name, **kw)
    reasons, gates = [], {}
    excess = r.get("excess") or []

    prior = con.execute("SELECT COUNT(*), VAR_SAMP(sharpe) FROM evaluation_runs").fetchone()
    trials = (prior[0] or 0) + 1

    t = _t(excess)
    p = 2 * (1 - N.cdf(abs(t)))
    gates["significance"] = bool(excess) and p * trials < ALPHA
    if not gates["significance"]:
        reasons.append(f"daily excess t={t:+.2f}, p={p:.3g} x {trials} trials "
                       f"is not < {ALPHA}")

    gates["costs"] = bool(excess) and mean(excess) > 0
    if not gates["costs"]:
        got = mean(excess) if excess else float("nan")
        reasons.append(f"excess net of costs {got:+.3%} per session "
                       f"(turnover {r.get('turnover', 0):.1%}/session, "
                       f"round trip {r.get('cost_round_trip', 0):.2%})")

    sr_var = prior[1] if prior[1] is not None else (1 / max(len(excess), 1))
    sr, dsr = deflated_sharpe(excess, trials, sr_var)
    gates["deflated_sharpe"] = dsr >= MIN_DSR
    if not gates["deflated_sharpe"]:
        reasons.append(f"deflated Sharpe {dsr:.2f} < {MIN_DSR} after {trials} trials")

    by_year = defaultdict(list)
    for row, x in zip(r.get("rows", []), excess, strict=True):
        by_year[row["date"].year].append(x)
    sign = 1 if mean(excess or [0]) >= 0 else -1
    years = {y: mean(v) for y, v in by_year.items()}
    agree = sum(1 for v in years.values() if v * sign > 0) / len(years) if years else 0
    gates["walk_forward"] = agree >= MIN_YEAR_AGREEMENT
    if not gates["walk_forward"]:
        reasons.append(f"excess sign held in {agree:.0%} of years "
                       f"(< {MIN_YEAR_AGREEMENT:.0%})")

    regimes = dict(con.execute("SELECT business_date, regime FROM market_regime").fetchall())
    by_reg = defaultdict(list)
    for row, x in zip(r.get("rows", []), excess, strict=True):
        by_reg[regimes.get(row["date"], "UNKNOWN")].append(x)
    against = {g: _t(v) for g, v in by_reg.items() if _t(v) * sign < -REGIME_T}
    gates["regime"] = not against
    if against:
        reasons.append("excess significantly reversed in " + ", ".join(
            f"{g} (t={tt:+.1f})" for g, tt in against.items()))

    held = r.get("avg_held", 0)
    gates["capacity"] = held >= MIN_NAMES
    if not gates["capacity"]:
        reasons.append(f"{held:.0f} names held on an average session < {MIN_NAMES}")

    verdict = "PROMOTE" if all(gates.values()) and excess else "REJECT"
    if not excess:
        reasons.append("no sessions with outcomes")
    out = {"version": VERSION, "strategy": name, "verdict": verdict, "gates": gates,
           "reasons": reasons, "trials": trials, "days": r.get("days", 0),
           "mean_excess": mean(excess) if excess else float("nan"), "excess_t": t,
           "sharpe": sr, "deflated_sharpe": dsr, "turnover": r.get("turnover", 0),
           "avg_held": held, "invested_days": r.get("invested_days", 0),
           "excess_by_year": years,
           "excess_by_regime": {g: mean(v) for g, v in by_reg.items()}}
    if record:
        con.execute("""INSERT INTO evaluation_runs (run_at, version, feature, horizon,
                       params, dates, mean_ic, ic_t, sharpe, deflated_sharpe, verdict,
                       reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), VERSION, name, 1,
                     json.dumps({k: v for k, v in kw.items() if isinstance(v, (int, float, str))}),
                     out["days"], out["mean_excess"], t, sr, dsr, verdict,
                     json.dumps(reasons)])
    return out


# A candlestick pattern fires on one bar and says nothing about when to leave, so the
# strategy is "hold for N sessions after it fires". That is stateless - the position is
# simply whether the pattern occurred in the trailing window - which sidesteps the
# question of an exit rule the pattern itself does not provide.
for _k in ("hammer", "shooting_star", "bullish_engulfing", "bearish_engulfing",
           "morning_star", "marubozu_bull", "doji"):
    INDICATORS[f"fired_20_{_k}"] = (
        f"COALESCE(SUM(k_{_k}) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 0) > 0")
    STRATEGIES[f"candle_{_k}_hold20"] = {
        "entry": f"fired_20_{_k}", "exit": f"NOT fired_20_{_k}",
        "needs": [f"fired_20_{_k}"],
        "claim": candles.PATTERNS[_k]["means"] + " Held 20 sessions after it fires.",
        "source": f"features/candles.py; prior stated there: {candles.PATTERNS[_k]['prior']}",
    }


# An event is a moment, so the strategy is "hold for N sessions after it fires" - the same
# stateless construction the candlestick patterns use, which sidesteps inventing an exit
# rule the event itself does not provide. Horizons match the analogue measurement window.
from ..features import event_flags as _event_flags  # noqa: E402

_EVENT_HOLD = 90


def _register_event_strategies() -> None:
    """Add one hold-N strategy per tracked event type.

    Wrapped in a function rather than run at module scope on purpose: a bare loop here
    leaked its variables into the module namespace, and one of them was named ``_t`` -
    which silently replaced the module's t-statistic function with the string 'BUYBACK'.
    Every strategy validation would have reported a broken t. Two unrelated tests caught
    it, which is the only reason it was not committed.
    """
    for event_type, measured in _event_flags.TRACKED.items():
        col = "e_" + event_type.lower()
        key = f"held_{_EVENT_HOLD}_{event_type.lower()}"
        INDICATORS[key] = (
            f"COALESCE(SUM({col}) OVER (w ROWS BETWEEN {_EVENT_HOLD - 1} PRECEDING "
            f"AND CURRENT ROW), 0) > 0")
        STRATEGIES[f"event_{event_type.lower()}"] = {
            "entry": key, "exit": f"NOT {key}",
            "needs": [key],
            "claim": f"Hold for {_EVENT_HOLD} sessions after a high-materiality "
                     f"{event_type} filing.",
            "source": f"h16. Measured base rate: {measured}",
        }


_register_event_strategies()
