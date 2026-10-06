---
type: research
tags:
  - research
  - result
  - features
  - smart-money-concepts
  - machine-learning
date: 2026-10-06
status: result
---

# Result of the deep feature library

Pre-registration: [[PRE-REGISTERED deep feature library and learned combinations]] (with its amendment adding
Fibonacci and the other named families, committed before any result). 2,096 names, 4.0 million daily bars, a panel of
267,000 (session, name) rows every 5th session 2016-2026, the 500 most liquid names per session, **161 features**.

## Test 1 - every feature alone (322 feature x horizon tests)

* 131 survive the 5% false-discovery rate across the whole library; **26 count** (FDR + same sign at |t| >= 2 in at
  least 3 of 4 liquidity tiers + the same sign at |t| >= 2 in the held-out years 2022-2026).
* What the 26 are: **volatility and lottery-ness** (vol 5/20/60/250, ATR, Bollinger width, wide-range bars, largest
  daily return in 20 sessions - high values do worse), **short-term reversal** (5-day return, distance to the 10-day
  average, Connors RSI, down-streaks, force index, Camarilla position, an upward break of structure - all "what went up
  this week gives some back next week"), **illiquidity** (Amihud), **momentum** (12-1, 52-week-high distance, days since
  the high) and two Fibonacci features on the 250-session swing.
* **Almost all of them predict through the bottom of the ranking.** Their long-only top quintile earns about zero over
  5 sessions (-0.04% to +0.05%); only momentum and the 52-week high put money in the top quintile (+0.16-0.18%
  per 5 sessions). The same lesson as low volatility in the half-life table: a feature can rank well and still not
  pay a long-only book.

**L1** (fewer than 15 count, mostly known families): **wrong on the number** (26), **right on the substance** - every
counting feature is a version of volatility, reversal, liquidity or momentum, already in the system.

### The named families

| family | best cell | counts? |
|---|---|---|
| Fair value gaps (new, open, distance, retest, weekly) | bullish new, IC -0.017 (t -5.8) - the wrong sign for the claim | **no** |
| Liquidity sweeps, break of structure, order blocks, HH/HL | break of structure up, IC -0.010 (t -4.3): it *reverses* | only BOS-up, as reversal |
| Fibonacci retracement / nearness on the 250-session swing | IC -0.009 / +0.010 (t 2.5-2.6) | yes, both - see below |
| Fibonacci golden pocket, 1.618 extension, Fibonacci MAs | golden pocket t 0.98 and -0.75 | **no** |
| Pivots (classic, weekly), Camarilla | Camarilla position IC -0.016 (t -4.4) | Camarilla only, as reversal |
| Ichimoku, Parabolic SAR, Supertrend, Heikin-Ashi | ha streak, psar, tenkan: t -4.6 to -5.4 in 2 tiers | **no** (2 tiers, not 3) |
| Gann square of nine, round numbers, harmonic Gartley | t -1.0, -2.4, +0.9 | **no** |
| Candles (engulfing, stars, hammer, harami, soldiers, crows, doji, marubozu) | three crows +0.013 (t 4.9), 2 tiers | **no** |
| Wyckoff compression | +0.009 (t 4.1), 2 tiers, held-out t 1.1 | **no** |

The two Fibonacci features that count are not the Fibonacci story. `fib_retrace_250` is how much of the last year's
swing has been given back - another way of measuring distance from the 52-week high, with an IC a quarter of that
signal's. `fib_near_250` is *positive*: names **far** from any Fibonacci level do very slightly better, the opposite of
levels acting as support. The golden pocket, the extension and the Fibonacci averages are null.

**L4** (no Fibonacci, Gann, harmonic, pivot or round-number feature counts): **wrong on the letter** (Camarilla and two
Fibonacci measures count), **right on the claim** - none counts as the level-respecting effect it is named for.

## Test 2 - the fair value gap claim

767,000 gaps. Gaps fill often: **48% of bullish gaps within 5 sessions, 71% within 20; bearish 55% and 76%.**

After a fill-and-hold retest (price trades back into the gap and closes beyond its far edge), against the universe on
the same days, one observation per day:

| | next 5 sessions | next 20 sessions |
|---|---|---|
| bullish gap retest | **-0.13%** (t -3.8) | -0.13% (t -1.8) |
| bearish gap retest | **-0.23%** (t -7.9) | **-0.54%** (t -10.4) |

The bullish half of the claim fails - a "held" bullish retest is followed by *under*performance. The bearish half holds,
strongly: after a bearish gap is retested and rejected, the stock keeps lagging. In a cash market that cannot short
delivery, that is a **name to avoid**, not a trade. This test did not have a held-out split or tiers (the registration
listed tiers for Test 1 only); before the brain uses it, an "avoid names after a rejected bearish FVG retest" filter
needs its own pre-registered held-out test.

**L2** (gaps fill >60%, a retest earns under 0.2% in 5 sessions, no FVG feature counts): fill rate **right**, no FVG
feature counting **right**, the retest **wrong** for bearish gaps (0.23% at 5 sessions and 0.54% at 20, both strong).

## Test 3 - learned combinations, walk-forward

LightGBM on all 161 features (hyperparameters fixed in advance), trained each year on data ending 30 days before it,
scored monthly on the 500 most liquid names with sized costs:

| period | book | top 20 net (t) | top 50 net (t) | turnover (top 20) |
|---|---|---|---|---|
| 2019-21 | model | +0.89% (1.5) | +0.50% (1.0) | 89% |
| | champion | +1.92% (2.7) | +1.04% (1.8) | 43% |
| **2022-26** | **model** | **+0.77% (2.3)** | **+0.59% (2.3)** | 86% |
| | v2 | +0.14% (0.3) | +0.49% (1.4) | 68% |
| | champion | +0.48% (0.9) | +0.53% (1.4) | 39% |

Model minus champion in 2022-26: **+0.29% a month (t 0.5)** for the top 20, +0.06% (t 0.1) for the top 50. In 2019-21
it lost to the champion by about 1% a month.

Against the criteria: net positive with t >= 2 in 2022-26 - **yes, both books**; beats the champion by more than 0.10% a
month - **top 20 yes (+0.29%), top 50 no (+0.06%)**. So the top-20 model **qualifies as a live challenger**, and only as
that: its lead over plain momentum is not distinguishable from zero, it lost to momentum in the earlier years, and it
trades twice as much.

What it leaned on (gain, trained before 2022): the 50/200 cross, 250- and 60-day volatility, the 20/50 cross,
momentum, Wyckoff compression, Supertrend, illiquidity, the 200-day slope, marubozu candles, both golden pockets,
1-year return, ATR, unfilled down-gaps, turnover, the Ichimoku cloud - trend and volatility first, with the named
indicators contributing small amounts of interaction.

**L3** (beats v1, does not beat the champion by 0.10% with t >= 2): **right** - it beats it by 0.29% with t 0.5.

## What follows

* The **learned model (top 20) joins the locked champion competition** as a challenger from 2026-10-07; it qualified on
  its pre-registered terms, and the live record decides.
* No Fibonacci, smart-money, pivot, Gann, harmonic or candle feature joins the scanners: none counts as the effect it is
  named for.
* The **bearish FVG retest** is the one new idea with a strong number; it gets its own pre-registered held-out test as an
  avoid-filter before anything uses it.
* Depth helped in one specific way: not by finding a new edge in any named indicator, but by letting a model combine
  trend, volatility and liquidity a little better than momentum alone - in the recent period, not significantly.
