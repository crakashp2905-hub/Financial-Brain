---
type: research
tags:
  - research
  - pre-registration
  - replication
  - validation
date: 2026-10-01
status: registered, not yet run
---

# PRE-REGISTERED: out-of-sample test of the eight survivors

**This note is written and committed before the test is run.** Everything below is a prediction. The
result goes in a separate note so this one cannot be edited after the fact, and the git history is the
proof of ordering.

The reason for the ceremony: every edge this project found died on its first honest out-of-sample test,
and in each case the test came *after* the discovery had already been written up as promising. A swept
horizon worth +246% in sample returned -7.49%. An event filter worth +82.9 points cost 4.53. Both had
cleared the threshold in front of them. The pattern is not bad luck, it is the ordering.

## The held-out sample

There is no unseen *time* window - 2015 to 2026 has all been looked at. But there is an entirely unseen
*cross-section*. Every measurement in
[[Four edges that replicate, and the chart patterns that do work]] used turnover ranks **1 to 500**.
Nothing has ever been measured on:

    tier         ranks        distinct names   median adv20
    deep         501-1000              2,324     Rs 3.21 cr
    micro       1001-1500              2,398     Rs 0.37 cr

These test whether the **effect exists**, not whether it is tradeable. At Rs 0.37 cr a day the micro
tier cannot carry capital, and that is fine: the question here is whether the phenomenon is real, and a
phenomenon that stops at rank 500 is a different claim from one that continues.

## What is being tested, unchanged

No refitting, no reselection, no new parameters. The eight survivors exactly as they stand:

**Four tier-replicated features** (cross-sectional IC, h20, same method):

    vol_60            predicted NEGATIVE
    mom_12_1          predicted POSITIVE
    dist_52w_high     predicted POSITIVE
    atr_pctile_250    predicted NEGATIVE

**Four candle patterns** (matched event study, h5, matched on mom_12_1 and ret_20d):

    k_bearish_engulfing   predicted NEGATIVE
    k_marubozu_bull       predicted POSITIVE
    k_shooting_star       predicted NEGATIVE  (h5 and h20)

## Pass and fail, stated now

**A feature passes** if it has the predicted sign in **both** new tiers with |t| >= 2.0 in each. Same
rule as the original replication, applied to subsamples that had no part in selecting it.

**A feature fails** if it flips sign in either tier, or if |t| < 2.0 in either.

**The census as a whole**: eight signals, so a pass rate near zero is the honest expectation given this
project's history, and anything above half would be the first genuinely out-of-sample result it has.

## The sharp prediction, which is the interesting one

Three of the four features showed a **monotone size gradient** - the IC rising steadily as names get
smaller:

    feature          large     next      mid     small
    mom_12_1        +0.024   +0.038   +0.040   +0.053
    dist_52w_high   +0.016   +0.036   +0.050   +0.067
    vol_60          -0.033   -0.033   -0.056   -0.068

If that gradient is a real property of the effect, it must **continue**:

    mom_12_1        deep IC > +0.053,  micro IC > deep IC
    dist_52w_high   deep IC > +0.067,  micro IC > deep IC
    vol_60          deep IC < -0.068,  micro IC < deep IC

This is a much harder prediction than a sign, and it is falsifiable in a way a sign is not. If the
gradient flattens or reverses at rank 500, then whatever produced it inside the top 500 was not a size
effect and the monotonicity I called "the strongest evidence here" was a coincidence across four points.

I expect the signs to hold and I am genuinely unsure about the gradient.

## What would make me wrong in a way that matters

A plausible confound: the deep and micro tiers are illiquid, and illiquid names have wider spreads and
more stale prices. Both inflate measured short-horizon reversal and can inflate momentum ICs through
price staleness - a name that did not trade yesterday shows yesterday's close, so its "return" arrives
late and looks like continuation. If momentum's IC balloons in the micro tier, **staleness is the first
explanation to rule out, not evidence for the gradient.** The result note has to check the share of
non-trading sessions per tier before claiming the gradient continued.

Registered 2026-10-01, before running. Result: [[Result of the pre-registered out-of-sample test]].

Related: [[Four edges that replicate, and the chart patterns that do work]] ·
[[Naive momentum beats it, and the horizon was a fit]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]]
