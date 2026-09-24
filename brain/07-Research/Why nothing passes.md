---
type: research
tags:
  - research
  - diagnosis
  - costs
date: 2026-09-24
---

# Why nothing passes

Fifty-seven trials, fifty-six rejections and one void. This is the diagnosis, in order of
how much evidence stands behind each claim.

## 1. Mostly, there is no edge - and that is the expected result

Most published strategies do not survive out of sample in a different market a decade
later, and the ones that do are the ones everyone already trades. Of the 57 trials, the
overwhelming majority sit at |t| < 2 - not "killed by a harsh gate" but *nothing there*.
The four bounded oscillators came in at |t| < 0.7. Seven candlestick patterns were all
negative. That is not a system failing; that is a system reporting correctly.

Any diagnosis that skips this and goes straight to "the gates are too strict" is
motivated reasoning.

## 2. But the gate that kills the survivors is **cost**, and cost is assumed, not measured

Read the rejection reasons and one word recurs. Of the handful of signals with real
signal, almost every one dies on turnover rather than significance.

And the cost number is 71% guesswork. For the `mid` bucket:

| Component | bps (round trip) | |
|---|---:|---|
| **Impact** | **50.0** | **an estimate from a hardcoded table** |
| STT | 20.0 | statutory, exact |
| Exchange, SEBI, stamp, GST | 0.9 | exact |

`costs/india.py` says so itself: impact is *"the single largest and least certain term
outside the top few hundred names."*

Checked against our own data using Amihud (2002) illiquidity, read as a Kyle lambda, for a
Rs 10 lakh order:

| Bucket | Median turnover | Amihud-implied | Assumed |
|---|---:|---:|---:|
| mega | Rs 296cr | 0.0 bps | 5 bps |
| large | Rs 66cr | 0.1 bps | 10 bps |
| mid | Rs 10cr | 0.7 bps | 25 bps |
| small | Rs 1.0cr | 5.3 bps | 60 bps |
| micro | Rs 0.05cr | 93.5 bps | 150 bps |

**Amihud is not right either** - it ignores the bid-ask spread, which is a hard floor and
is realistically 5-15 bps in Indian mid-caps, and it measures the average day's move per
rupee of total volume rather than the marginal cost of an aggressive order. The true
number is between the two columns. But our figure is very likely too pessimistic for
anything liquid, and it is the assumption gating 56 rejections.

## 3. The one significant signal sits inside that uncertainty band

`above_ma200` clears the Bonferroni gate at **t = +3.69** and grosses **+0.24% per
period** at 70% turnover:

    assumed mid (current)          round trip 0.709%   ->   net -0.256%
    if impact were 10bps/side      round trip 0.409%   ->   net -0.046%
    if impact were  5bps/side      round trip 0.309%   ->   net +0.024%
    statutory only, zero impact    round trip 0.209%   ->   net +0.094%

The statistics are identical in every row. **We do not currently know whether the only
significant signal we have makes money**, because the number that decides it was never
measured.

## 4. Turnover is the mechanism, and it is controllable

The same claim at two turnovers, from two different harnesses:

| | t | turnover | net |
|---|---:|---:|---:|
| `above_ma200` | +3.69 | 70%/period | -0.26% |
| `ma_double_cross_50_200` | +2.74 | 0.78%/session | **+0.0195%** |

Smoothing both legs of a trend rule cut turnover roughly fivefold and flipped the cost
outcome. Turnover is not fate; it is a design variable that has never been optimised here
because nothing had survived long enough to be worth optimising.

## 5. Structural choices that have never been varied

Every test so far has used the same three settings, and none was ever justified by a
measurement:

* **One horizon.** Everything at 20 sessions. Slow trend - the only thing showing
  significance - is being forced to rebalance monthly.
* **The whole liquid universe.** ~2,000 names including the `micro` bucket, where impact is
  genuinely 100+ bps and the data is worst. Published results are usually large-cap.
* **One signal at a time.** No combination has ever been tested, which is the correct
  order of operations but means the ceiling is unknown.

## What to do, and the trap to avoid

**The trap:** lowering the cost assumption until something passes. That is the exact
behaviour [[Alpha Validation Firewall]] exists to prevent, and it would be worse than
every mistake this project has made so far, because it would be deliberate.

The protection is procedural: fix the cost model **once**, with a written methodology
decided before re-running anything, then re-run everything under it and report old and new
side by side. A constant revised for a stated reason is a code change; a constant revised
until an answer appears is fraud.

**In order:**

1. **Measure impact properly.** A spread floor plus a size-dependent Amihud term, fitted
   per liquidity bucket, replacing the hardcoded table. Highest leverage because it gates
   everything, and the current number is the least defensible input in the system.
2. **The pre-registered low-turnover trend test** already named in
   [[Trend is the only thing that has cleared significance]]. The 70% versus 0.78% gap is
   the whole difference between the two halves of that result.
3. **Define a tradeable universe.** Top ~300 by liquidity, where impact is small *and*
   reliably estimated. Restricting to what can actually be traded is a strategy decision,
   not a fudge - but it changes the benchmark too, so it is a new test, not a rescoring.
4. **Vary the horizon** for slow signals: 60, 120, 250 sessions.

Related: [[Trend is the only thing that has cleared significance]] ·
[[Charts, candles and indicators]] · [[Alpha Validation Firewall]] · [[MOC Strategies]]
