---
type: research
tags:
  - research
  - paper-trading
  - method
  - costs
date: 2026-09-28
updated: 2026-09-28
---

# The engine was the strategy's biggest short position

The question was narrow: does `dist_52w_high`'s **+19.11% excess** survive a universe chosen without
hindsight? The answer is that the question could not be asked yet, because the instrument was broken
in five places - and the strategy underneath turned out to be far *better* than the broken instrument
said, not worse.

Every number below is the corrected one. The superseded figures are kept where they say something
about the size of the error.

## The test that found four of the five

A book holding its **entire eligible universe** at equal weight *is* the control, by construction.
Its excess must be zero. Over eleven years it came back at **-140.27%**.

| | floor on the whole-universe book |
|---|---|
| as first written | **-140.27%** |
| after adding a trim leg | -106.68% |
| gross on both sides | -52.52% |
| keyed on lineage instead of ISIN | **+34.26%** |

**`_rebalance` could not trim.** It sold names that left the target and bought names that entered, but
for a name that *stayed* it would buy up to the equal weight and never sell down to it. The book was
buy-and-hold with additions, drifting into whatever had run up, compounding over 134 periods.

**The trim then fired on the cost drag alone.** Paying costs lowers equity, which lowers the target
weight, which makes every position overweight by a rupee. A static book with a constant ranking and
constant prices re-traded every name at two consecutive rebalances. Fixed with a no-trade band
(Constantinides 1986) - the same object the decision compiler already uses, now a run parameter.

**The control was survivorship-biased.** It priced the universe at both ends of the window, which
silently keeps only names that still had a price at the end. It reported the eleven-year universe at
**+307%**. An intermediate fix - chaining the daily cross-sectional mean - removed survivorship and
introduced a *rebalancing bonus* instead, harvesting the cross-sectional variance of 1,700 names at
zero cost, for **+356%**. The third version chains equal-weight returns over the point-in-time
eligible universe at the strategy's own rebalance frequency: **+329.93%**.

**The excess was net against a cost-free index.** The strategy paid for turnover the control never
pays. Gross on both sides, 54 of those points were brokerage.

## And the fifth, which was the biggest: an ISIN is not a company

`security_lineage` maps many ISINs to one lineage, and 843 of this database's 17,060 ISINs are
superseded. The engine keyed positions, prices, the target book and the control on **ISIN**, so the
day a held name's code changed its price lookup returned nothing and the position was frozen at cost -
counted in `stale_marks` and carried there for the rest of the run while the company kept trading.

    429 of 3,340 lineages (12.84%) change ISIN inside 2015-12..2026-09
    117 of 2,934 lineages ( 3.99%) change ISIN inside 2024-10..2026-09

Those are not a random 13% of names. They are precisely the ones that had corporate events.

Two more consequences, both silent:

**`costbook.buckets` is keyed on (date, lineage) and always was.** Feeding it ISINs meant every
superseded name missed its bucket and fell back to `"micro"`, the most expensive tier - so they paid
small-cap impact *on top of* losing their prices. In a fixture where no ISIN equals its lineage this
charges **160.4 bps a leg** instead of the correct tier.

**The target book could hold one company several times.** `features` is keyed on lineage; joining
`security_lineage` to recover an ISIN turned one feature row into one row per ISIN the company ever
had, so a lineage with three ISINs could fill three of the top twenty slots with itself. The join was
never needed.

Found while building the forecasting harness, where the same bug handed a foundation model a price
series ending **2019-09-19** for a 2026 forecast: lineage INE040A01034 has 2,903 sessions to 2026,
its older ISIN INE040A01026 has 1,166.

`calibrate()` now measures the floor, because it is the **resolution of the instrument** and it grows
with the number of periods.

## What the universe question actually answered

Two years, close fills, matched controls, against a two-year floor of **+1.54%**:

    universe                          names     return   universe     EXCESS
    A hindsight-selected (minute bars)  100    +29.73%    +29.49%     +0.24%
    B clean (turnover before window)    100     -1.66%     -2.63%     +0.97%
    C unrestricted, top 20 of 1,676    1676    -19.91%     -3.29%    -16.62%

The original **+19.11%** was **+0.24%**. **All three are inside the floor**, so the honest answer to
"does the excess survive a clean universe" is that at h20 with twenty names over two years there was
never an excess to survive. The 62% overlap between the two 100-name universes is the rest of the
story: the minute-bar list was substantially a list of what went up.

## The finding that got much stronger: gross is huge, net depends entirely on the horizon

Eleven years, top 100. **Each row must be read against the floor at its own rebalance**, and the floor
grows sharply with the horizon: fewer rebalances means less trimming, so weights drift further inside
the band and the whole-universe book picks up more of a momentum tilt the cost-free control never
gets. Measured: **+34.26% at h20**, **+130.79% at h60**. The h120 and h250 floors are not yet
measured and are presumably larger again.

    rebal  band       net    universe      EXCESS       gross    costs   Sharpe   maxDD
       20  0.20  +361.97%   +329.93%     +32.04%    +705.59%   150.1%    +0.96  -42.0%
       60  0.20  +575.32%   +328.76%    +246.55%    +742.34%    81.2%    +1.18  -39.3%
      120  0.20  +460.82%   +335.26%    +125.56%    +531.90%    34.5%    +1.08  -42.0%
      250  0.20  +346.29%   +317.39%     +28.90%    +373.58%    15.0%    +0.99  -38.7%
       20  0.50  +369.22%   +329.93%     +39.29%    +702.62%   150.9%    +0.97  -40.9%
       60  0.50  +579.65%   +328.76%    +250.89%    +738.55%    81.4%    +1.18  -39.0%
      250  0.50  +353.59%   +317.39%     +36.20%    +380.05%    15.1%    +0.99  -39.0%

Read the **gross** column first, because it is the signal with costs held out: flat from h20 to h60
(+705.59% to +742.34%), then falling off a cliff at h120 (+531.90%) and h250 (+373.58%). The signal
decays somewhere past 60 sessions. Costs fall monotonically the other way, from 150.1% of opening
equity to 15.0%. **h60 is where the two curves cross**, and it is an interior optimum rather than an
edge of the grid, which is the one thing that makes a swept parameter believable.

Every net excess is now positive - unfreezing one name in eight was worth more than every other fix
combined. But h60's **+246.55%** clears its own floor by about **116 points**, not the 212 an earlier
version of this note claimed: that comparison used the h20 floor against an h60 result, which is
comparing two different instruments and flattered the margin by roughly a hundred points.

And the result does not survive being chosen honestly.
[[Naive momentum beats it, and the horizon was a fit]] walks the horizon selection forward: h60 is
picked on every training window and returns a mean of **-7.49%** out of sample, while h120 - which
trains worst in every fold - is the only horizon with a positive out-of-sample mean. The table below is
in-sample.

The band still barely matters: 20% and 50% differ by four points out of 250. Turnover comes from names
entering and leaving the top 100, not from weights drifting, so widening the band is not a lever and
lengthening the horizon is.

## What this is not

**The floor is +34.26% and still not zero.** It changed sign rather than vanishing. A whole-universe
book now *beats* its own control by 34 points over eleven years, which is the no-trade band letting
winners run up to 20% past target between rebalances - a momentum tilt worth roughly 0.9% a year that
the cost-free control does not get. That is the instrument's resolution, and any excess of that size
means nothing.

**Seven configurations are spent on the sweep.** h60 was chosen by looking, on the same eleven years,
after 150 trials. [[A hundred and fifty trials are not a hundred and fifty discoveries]] applies in
full.

**The out-of-sample test has since been run and it fails.** See
[[Naive momentum beats it, and the horizon was a fit]]. Everything in the sweep table is in-sample and
should be read as such.

**Costs at h60 are still 81.2% of opening equity** over eleven years, about 5.6% a year. The strategy
is not cheap; it is merely no longer paying more than it earns.

Related: [[The first end-to-end run, and the control that makes it readable]] ·
[[The cost number that decided everything]] · [[Asking the question the factor actually claims]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]] · [[h20]] · [[MONEY GATE]] ·
[[Kronos is confidently wrong]] ·
[[Naive momentum beats it, and the horizon was a fit]]
