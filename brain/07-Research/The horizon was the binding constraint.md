---
type: research
tags:
  - research
  - strategy
  - costs
date: 2026-09-27
---

# The horizon was the binding constraint

Eighty-six trials concluded that the edges in this market are real and too small to pay for.
That conclusion was drawn almost entirely at **one holding period**. Cross-sectional tests
ran at h = 20 sessions; the intraday systems closed the same day; the stateful rules turned
over weekly. The horizon was never swept, and it is the one variable that changes the
arithmetic without changing the signal.

## The arithmetic, before the measurement

A round trip near 80 bps has to come out of the alpha earned **over the hold**. So the
per-session hurdle is 80/h:

    h =   2 sessions ->  40.0 bps a session
    h =   5           ->  16.0
    h =  20           ->   4.0
    h =  40           ->   2.0
    h = 250           ->   0.3

Measured alphas here run 1-10 bps a session. At h = 20 the hurdle is 4.0 and the edges lose.
At h = 40 it is 2.0. **Testing at h = 20 was testing just inside the losing side of a line
nobody had drawn.**

## `mom_12_1` is net positive at every horizon tested

Twelve-minus-one-month momentum (Jegadeesh & Titman 1993), top quintile against the
equal-weighted universe, costed per rebalance on the book's own liquidity mix:

| h | rebalances | IC t | gross/period | turnover | net/period | net/session | net t |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 1326 | +7.18 | +0.093% | 7% | +0.032% | +0.0159% | +1.83 |
| 5 | 530 | +6.15 | +0.215% | 12% | +0.114% | +0.0228% | +2.51 |
| 20 | 132 | +3.78 | +0.758% | 27% | +0.541% | +0.0270% | +2.63 |
| **40** | **65** | **+3.95** | **+1.501%** | **36%** | **+1.202%** | **+0.0301%** | **+2.85** |
| 60 | 43 | +3.44 | +1.886% | 45% | +1.521% | +0.0254% | +2.53 |
| 250 | 10 | +2.03 | +6.650% | 82% | +5.987% | +0.0240% | +1.86 |

**Turnover is 7% to 36%.** That is the whole reason it survives where trend does not: a
12-month lookback with a one-month skip barely reorders from month to month. `above_ma200`
turns over 63-86% at every horizon and pays the toll ten times as often.

Two curves cross at h = 40. The IC **decays** with horizon (+7.18 down to +2.03) because a
momentum rank predicts less far ahead. The cost **amortises** with horizon. Net is the
product, and it peaks in the middle - which is what a real effect against a real cost looks
like, and is the opposite of the sharp single-point spike that fitting produces.

## `above_ma200` also crosses, and later

| h | IC t | gross/period | turnover | net/period | net t |
|---:|---:|---:|---:|---:|---:|
| 20 | +3.69 | +0.281% | 70% | −0.296% | −2.18 |
| 40 | +4.13 | +0.504% | 73% | −0.092% | −0.38 |
| 60 | +3.63 | +0.831% | 77% | **+0.209%** | +0.63 |
| 250 | +3.19 | +5.075% | 86% | +4.371% | +2.69 |

Its IC does **not** decay - it sits between +3.19 and +4.35 across the whole range, which is
the signature of a slow signal. What killed it was paying 0.82% seventy times a period's
worth. Held for a quarter instead of a month it is net positive. [[h12]] concluded slow trend
and its turnover were inseparable; that was true *at h = 20*, and the horizon was the
variable it did not vary.

## Low volatility fails for a reason the harness cannot see

`vol_60` has the strongest IC of anything measured here - **t = +12.30** at h = 2 - and its
top quintile's raw excess is **negative at every horizon**. Both are correct. The low-volatility
anomaly is a claim about **risk-adjusted** return, and the lowest-volatility quintile is not
supposed to out-earn the universe in raw percentage terms; it is supposed to earn nearly as
much with far less variance.

This harness measures raw excess over an equal-weighted benchmark. It therefore cannot test
the low-volatility anomaly at all, and has now rejected it three times on a criterion it does
not claim to meet. That is a gap in the evaluation, not a finding about the factor, and it is
the first identified in the firewall itself rather than in a cost or a feature.

## What this does not establish

**The sweep was not pre-registered.** Forty-five (feature, horizon) combinations were
examined after seeing the h = 20 results, which is a search, and `benchmark.evaluate` records
nothing - so those trials were being spent invisibly. They are now in `evaluation_runs`,
labelled as an unregistered exploratory sweep, and the Bonferroni bar rises for everything
after them.

At 126 trials that bar is **|t| > 3.54**. `mom_12_1` at h = 40 reaches **+2.85**. It is the
closest anything has come on a net basis with a mechanism behind it, and it **does not pass**.

What is owed before it means anything:

1. a pre-registered out-of-sample test on a period held back from this sweep;
2. separation from a **turnover-matched synthetic null**, per
   [[Running the strategies on noise]] - a 36%-turnover rule scoring +2.85 has to be read
   against what 36% turnover scores on Brownian motion;
3. the walk-forward and regime gates, because a momentum book held 40 sessions in a market
   that rose over the sample is exposed to exactly the drift the excess-over-universe
   benchmark is meant to remove and may not fully.

## The reframing

The honest summary is no longer "the edges are too small to pay for". It is:

> **the edges were measured against a toll charged ten to a hundred times more often than
> the signal changes.**

Those are different claims and they point somewhere different. The first says look for bigger
edges, which is what [[A hundred strategies and the data that blocks half of them]] concluded
means fundamentals. The second says look for **slower** ones, which needs no new data at all.

Related: [[The toll was measured with one number and it needed thousands]] ·
[[Turnover explains 97% of it]] · [[h12]] · [[Running the strategies on noise]] ·
[[Alpha Validation Firewall]]
