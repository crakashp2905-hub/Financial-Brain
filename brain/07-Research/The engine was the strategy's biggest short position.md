---
type: research
tags:
  - research
  - paper-trading
  - method
  - costs
date: 2026-09-28
---

# The engine was the strategy's biggest short position

The question was narrow: does `dist_52w_high`'s **+19.11% excess** survive a universe chosen without
hindsight? The answer is that the question could not be asked yet, because the instrument was
broken in four places - and the strategy underneath turned out to be *better* than the broken
instrument said, not worse.

## The test that found all four

A book holding its **entire eligible universe** at equal weight *is* the control, by construction.
Its excess must be zero. Over eleven years it came back at **-140.27%**.

Everything below was found by that one test, and none of it by care.

| | floor on the whole-universe book |
|---|---|
| as first written | **-140.27%** |
| after the trim leg | -106.68% |
| gross on both sides | **-52.52%** |

**`_rebalance` could not trim.** It sold names that left the target and bought names that entered,
but for a name that *stayed* it would buy up to the equal weight and never sell down to it. The book
was equal-weight buy-and-hold-with-additions, drifting into whatever had run up, compounding over
134 periods.

**The trim then fired on the cost drag alone.** Paying costs lowers equity, which lowers the target
weight, which makes every position overweight by a rupee. A static book with a constant ranking and
constant prices re-traded every name at two consecutive rebalances. Fixed with a no-trade band
(Constantinides 1986) - the same object the decision compiler already uses, now a run parameter.

**The control was survivorship-biased.** It priced the universe at both ends of the window, which
silently keeps only names that still had a price at the end. It reported the eleven-year universe at
**+307%**. An intermediate fix - chaining the daily cross-sectional mean - removed survivorship and
introduced a *rebalancing bonus* instead, harvesting the cross-sectional variance of 1,700 names at
zero cost, for **+356%**. The third version chains equal-weight returns over the point-in-time
eligible universe at the strategy's own rebalance frequency: **+333.86%**.

**The excess was net against a cost-free index.** The strategy paid for turnover the control never
pays. Gross on both sides, 54 of those points were brokerage.

`calibrate()` now measures that floor, because it is the **resolution of the instrument** and it
grows with the number of periods. Every excess this engine had reported was smaller than it.

## What the universe question actually answered

Two years, close fills, matched controls:

    universe                              names    return   universe    EXCESS
    hindsight-selected (minute bars)         100   +31.64%   +27.72%    +3.93%
    clean (turnover before the window)        99    -4.64%    -3.30%    -1.34%
    unrestricted, top 20 of 1,676           1676   -19.44%    -2.81%   -16.63%

So the honest reading of the original **+19.11%** is **+3.93%**, and **-1.34%** once the universe is
chosen without hindsight. The 55% overlap between the two 100-name universes is the whole story: the
minute-bar list was half a list of what went up.

But +3.93% is inside the floor, so none of these three numbers means anything on its own.

## The finding that survived: gross is huge, net is dead

Eleven years, top 100 of the eligible universe, against a floor of **-52.52%**:

    gross    +484.70%      universe +333.86%      +151 points
    net      +199.27%      universe +333.86%      -135 points
    cost drag  124% of opening equity, about 10% a year

A 20-session full-turnover rebalance at an 80 bp round trip *is* 10% a year. The signal is real and
large; the horizon spends all of it and more. This is the first end-to-end confirmation of
[[The cost number that decided everything]], and it reconciles the two measurements that looked like
they disagreed: the **+9%/yr** cross-sectional alpha in
[[Asking the question the factor actually claims]] was *gross*, so the corroboration was sound - both
routes were measuring a signal that costs then eat.

## And the horizon has an interior optimum

Eleven years, top 100, sweeping the rebalance interval and the no-trade band:

    rebal  band       net    universe     EXCESS      gross    costs   Sharpe
       20  0.20  +191.96%   +333.86%   -141.89%   +484.70%   124.2%    +0.80
       60  0.20  +356.81%   +335.49%    +21.31%   +491.87%    67.6%    +1.10
      120  0.20  +293.55%   +339.68%    -46.14%   +351.50%    31.4%    +1.00
      250  0.20  +224.35%   +315.49%    -91.13%   +244.18%    12.9%    +0.94
       20  0.50  +197.47%   +333.86%   -136.39%   +496.18%   126.0%    +0.81
       60  0.50  +365.24%   +335.49%    +29.75%   +501.17%    68.5%    +1.10
      250  0.50  +230.37%   +315.49%    -85.12%   +250.58%    13.0%    +0.94

Read the **gross** column first, because it is the signal with costs held out: flat from h20 to h60
(+484.70% to +491.87%), then falling off a cliff at h120 (+351.50%) and h250 (+244.18%). The signal
decays somewhere past 60 sessions. Costs fall monotonically the other way. **h60 is where the two
curves cross**, and it is an interior optimum rather than an edge of the grid, which is the one thing
that makes a swept parameter believable.

The band barely matters - 20% and 50% differ by 8 points out of 490 - which says the turnover is
coming from names entering and leaving the top 100, not from weights drifting. That is worth knowing:
widening the band is not a lever, lengthening the horizon is.

## What this is not

**Seven more configurations are now spent.** h60 was chosen by looking, on the same eleven years,
after 150 trials. [[A hundred and fifty trials are not a hundred and fifty discoveries]] applies in
full, and +21.31% excess over eleven years is about **+1.4% a year** net over its own universe - a
thin result to have paid a search for.

**The floor is still -52.52% and unexplained.** 7.1% of the book sits in cash because share counts
round down, which accounts for roughly half of it. The rest is the buy loop starving low-ranked
names when cash runs out mid-pass, and drift inside the band. Until that floor is near zero, an
eleven-year excess of +21.31% is not separable from the instrument.

**No out-of-sample window.** h60 was selected on 2015-2026 and tested on 2015-2026.

Related: [[The first end-to-end run, and the control that makes it readable]] ·
[[The cost number that decided everything]] · [[Asking the question the factor actually claims]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]] · [[h20]] · [[MONEY GATE]]
