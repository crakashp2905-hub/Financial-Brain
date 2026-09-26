---
type: research
tags:
  - research
  - firewall
  - method
date: 2026-09-26
---

# Running the strategies on noise

Geometric Brownian motion is not a strategy. It is the **null** - what prices would do
carrying no information at all - which makes it the right thing to run a strategy library
against. The question it answers is the one before "does this work": **would the harness
say it works if nothing were there?**

## Why this project needed it

Five measurement artifacts have been found here in a week, each producing a confident,
correctly computed, entirely false number: a benchmark returning 84% a year, a spread
estimator off by tenfold, candlestick patterns at t = 6 that were bid-ask bounce, a
session median nobody could buy, and a liquidity ranking that put a two-session IPO above
HDFC Bank.

Four of the five were caught by noticing an implausible number. That works and it depends
on someone looking. A synthetic control does not.

## The harness passes

200 synthetic names, matched drift and volatility, the real trading calendar, and no
structure of any kind:

| Strategy | null t | turnover |
|---|---:|---:|
| above_ma200_atr_band | +0.88 | 1.21% |
| above_ma200_band | +0.86 | 0.87% |
| ma_double_cross_50_200 | +0.85 | 0.53% |
| ma_cross_200 | -4.91 | 3.37% |
| turtle_20_10 | -7.69 | 5.73% |
| bollinger_reversion_20_2 | -8.92 | 7.16% |

**Nothing earns a positive edge on noise.** The largest is +0.88 against a Bonferroni bar
of 3.4. And the negative statistics **scale with turnover** - 3.37% gives -4.91, 5.73%
gives -7.69, 7.16% gives -8.92 - which is exactly the signature of no signal and pure cost
drag. The firewall does not manufacture edges, and now that is measured rather than
assumed.

## The reframing, which is the real finding

A strategy's own t-statistic conflates **signal** with **cost drag**. Measured against a
turnover-matched null, the ordering changes completely:

| Strategy | real t | null t | separation |
|---|---:|---:|---:|
| turtle_20_10 | -5.20 | -7.69 | **+2.49** |
| bollinger_reversion_20_2 | -6.75 | -8.92 | **+2.17** |
| ma_double_cross_50_200 | +2.74 | +0.85 | **+1.89** |
| ma_cross_200 | -3.82 | -4.91 | +1.09 |
| above_ma200_atr_band | +0.43 | +0.88 | **-0.45** |
| above_ma200_band | -0.45 | +0.86 | **-1.31** |

Turtle looks like the third-worst strategy tested and has the **largest separation from its
own null**. Donchian breakout does contain information; it is simply worth less than 5.73%
turnover costs. Reporting it as "rejected at t = -5.20" was accurate and uninformative.

## The uncomfortable part

`above_ma200_atr_band` was reported as the first trend strategy to pass the cost gate. It
scores **+0.88 on pure noise and +0.43 on real data** - below its own null. Whatever the
volatility-scaled band is doing, finding signal is not it, and the earlier write-up gave
it more credit than the evidence supports.

`above_ma200_band` is worse still at -1.31.

Neither conclusion changes a verdict: both were rejected. But "rejected while containing
nothing" and "rejected while containing something too small to harvest" are different
findings, and only the null separates them.

## What should change

**Every strategy should be reported against a turnover-matched null, not against zero.**
The absolute t answers "did this make money", which costs already dominate. The separation
answers "is there anything here", which is the question a research programme actually
needs.

## The bug this control produced on its own

The first version of the swap renamed the production tables and put views in their place.
`eod_prices` is a view; the rename raised; and the run aborted with `adjusted_prices`
already renamed and nothing in its place, leaving the real schema broken until it was
restored by hand.

Nothing that tests the harness should be able to damage the thing under test. It now
builds a separate database file and the real tables are only ever read.

Related: [[Alpha Validation Firewall]] · [[Why nothing passes]] ·
[[Dynamic rules, and the correction to h12]] · [[Intraday candles and the bid-ask bounce]]
