---
type: research
tags:
  - research
  - descriptive
  - signal-half-life
date: 2026-10-06
status: descriptive
---

# Signal horizons for a trader (half-life table)

Descriptive, not a new claim: it extends [[Result of half-life and second-order spillover]] and
[[Result of intraday half-life]] from IC to what a trader earns and pays. `evaluation/half_life_table.py`. The 500
most liquid names on each date, 2016-2026; rebalances spaced h sessions apart so each window is independent; the
top quintile by the signal against the universe mean; a 0.3% delivery round trip charged on the share of the
quintile replaced each time. In-sample, before tax.

| signal | best h (net per session) | top-quintile excess at 60 | hit rate at 60 | net at 60 after turnover | Sharpe at 20 |
|---|---|---|---|---|---|
| **12-1 momentum** | 5 (but positive at every h) | **+1.65%** | 68% | **+1.50%** | 0.96 |
| 52-week high | 10 | +1.36% | 70% | +1.16% | 0.72 |
| composite | 40 | +0.86% | 58% | +0.69% | 0.64 |
| low volatility | - | **-0.30%** | 53% | -0.43% | -0.19 |
| 20-day reversal | - | -1.32% | 23% | -1.57% | -0.43 |

Typical path of a top-momentum name over 60 sessions: median best point +14.0%, median worst point -9.6%.

## What a trader should read from it

1. **Momentum is the one signal that pays at every horizon** after its own turnover cost, and best per session held
   at short horizons only because the cost drag is spread over fewer days. This is the champion in the locked
   competition for a reason.
2. **Low volatility ranks well (positive IC at every horizon) but its top quintile under-earns the universe.** Its IC
   comes from the bottom of the ranking - the most volatile names doing worst - not from the low-volatility names
   doing best. A long-only book cannot harvest that.
3. **Reversal at 20 days is continuation, not reversal**, and costs the most to trade (turnover 81%).

## A flaw this exposes in today's ranking

The trader view's top 10 on 2026-10-06 were all flagged by the low-volatility scanner (RELIANCE, M&M, LT, ...). They
are there because every name in a composite decile gets the same expected return, and the risk penalty and the
cost then order the ties - both favour large, calm names. That is the same tie-breaking flaw that turned book-v1 into
a large-cap bet, and this table says the low-volatility end is the part of the ranking that does not pay long-only.
The fix belongs in a new, pre-registered ranking version (a continuous expectation led by the signals that pay
long-only, not a decile step), tested against the champion - not an edit to the live one. Until then the trader
view states it: these are the best by the current model, and the current model ties by cost.
