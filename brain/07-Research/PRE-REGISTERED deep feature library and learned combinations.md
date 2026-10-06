---
type: research
tags:
  - research
  - pre-registration
  - features
  - smart-money-concepts
  - machine-learning
date: 2026-10-06
status: pre-registered
---

# PRE-REGISTERED deep feature library and learned combinations

Written before anything is computed. The owner asked for far more depth - fair value gaps and the other
price-action ideas, many more indicators, and combinations of them. Depth is cheap to build and expensive to trust:
500 rules tested at 5% produce about 25 "discoveries" from noise, and picking combinations per stock was already
shown to manufacture a t = +6.2 edge that walked forward at -2.6 ([[Per-stock selection loses to not selecting]]).
So the depth is built with the controls that make it honest, fixed here.

## The library (`features/library.py`), every value known at the close of its day

* **Smart-money / price action:** bullish and bearish fair value gaps (3-candle gaps: candle 1's high below candle
  3's low, or the mirror) - new today, unfilled count over 20 sessions, distance to the nearest unfilled gap in ATR,
  a retest today (price trades into an unfilled gap and closes beyond its far edge), gap size in ATR; liquidity
  sweeps (a new 20-session high or low that closes back inside); break of structure above the last swing high or
  below the last swing low (5-bar fractals, confirmed only once known); bullish / bearish order-block distance;
  inside, outside, NR4, NR7 and wide-range bars; opening gaps and whether they filled the same day; close location,
  wick and body ratios; up/down streaks.
* **Candles:** doji, hammer, shooting star, bullish/bearish engulfing, harami, piercing / dark cloud, morning /
  evening star, three white soldiers / black crows, marubozu.
* **Indicators, several windows each:** RSI 2/7/14/28, stochastic 5/14, Williams %R, CCI, rate of change
  5/10/20/60/120/250, distance to moving averages 10/20/50/100/200 and their slopes, 20/50 and 50/200 crosses,
  Bollinger %b and bandwidth, Keltner position, Donchian position 20/55/250, ATR %, the ATR 5/20 squeeze, ADX and DI
  spread, MACD histogram, Aroon, realised volatility 5/20/60/250 and their ratios, skewness, the largest daily
  return in 20 sessions, days since the 52-week high, distance to the 52-week low.
* **Volume:** turnover ratios 5/20 and 20/250, on-balance-volume slope, Chaikin money flow, Amihud illiquidity,
  distance to the 20-session volume-weighted price.
* **Weekly timeframe:** weekly RSI 14, close against the 10- and 40-week averages, weekly fair value gaps.
* **Relative:** return against the Nifty 500 over 20 and 60 sessions.

## Test 1 - every feature alone

Every 5th session 2016-2026, the 500 most liquid names. For each feature and h in {5, 20}: mean rank IC (one value per
session, t across sessions, overlap-adjusted at h=20 by sampling every 20th session), the long-only top-quintile net
excess after a 0.3% round trip x its turnover, and the IC in each liquidity tier.

A feature **counts** only if, at some h: (a) it survives the **Benjamini-Hochberg false-discovery rate of 5% across
the whole library** (every feature x horizon is one test), (b) its IC has the same sign with |t| >= 2 in at least 3 of
4 tiers, and (c) its sign holds in the held-out years 2022-2026 with |t| >= 2.

## Test 2 - the fair value gap claim itself

Event study, all liquid names, daily bars: for every bullish (bearish) FVG, (i) the share filled within 5 and 20
sessions, (ii) the excess return over the universe in the 5 and 20 sessions after a fill-and-hold retest, against all
other names on the same days; one observation per day, tiers as above.

## Test 3 - learned combinations

A LightGBM model on the whole library (each feature cross-sectionally ranked), target the 20-session excess rank,
**walk-forward**: for each year 2019-2026 it trains only on data ending 20 sessions before that year starts
(an embargo, so no training label overlaps a test day), and predicts that year. Its top 20 and top 50 are scored in
the same harness as [[PRE-REGISTERED ranking v2]] - monthly rebalance, sized costs - against v1, v2 and the champion.

The model **qualifies as a challenger in the live competition** if, over 2022-2026, its net excess is positive with
t >= 2 and it beats the champion by more than 0.10% a month. Hyperparameters are fixed now (500 trees, learning rate
0.03, 31 leaves, minimum 200 rows per leaf, 0.7 feature and row sampling) and not tuned on results.

## Predictions

* **L1** Of about 120 features x 2 horizons, fewer than 15 count under (a)+(b)+(c), and most of those are versions of
  momentum, 52-week-high distance, volatility or short-term reversal already known.
* **L2** No fair value gap feature counts. Gaps fill often (more than 60% within 20 sessions - the price returns to
  most levels), and a fill-and-hold retest earns under 0.2% over the universe in 5 sessions.
* **L3** The learned model beats v1 in 2022-2026 but does **not** beat the champion by 0.10% a month with t >= 2:
  combinations of mostly-null features recover momentum and add turnover.
