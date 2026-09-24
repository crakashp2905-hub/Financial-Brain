---
type: research
tags:
  - research
  - costs
  - mistake
date: 2026-09-24
---

# The cost number that decided everything

Impact cost is 71% of a mid-cap round trip, it came from a hardcoded table, and it decided
fifty-six rejections. This is what happened when it was finally measured.

## What was at stake

`above_ma200` is the only signal in 57 trials to clear the Bonferroni significance gate -
t = +3.69, gross +0.24% a period. Its verdict depended entirely on a constant nobody had
checked:

    assumed 25 bps impact/side    net -0.256%/period
    at 10 bps/side                net -0.046%
    at  5 bps/side                net +0.024%
    statutory only                net +0.094%

Identical statistics in every row. The project did not know whether its best signal made
money.

## Corwin-Schultz, and why it failed

The principled way to get a spread from daily data is Corwin & Schultz (2012), which
separates spread from volatility using two-day high-low ratios. It needs only OHLC, which
this archive has.

It returned a **36 bps half-spread for the most liquid 5% of Indian equities** - a 72 bps
round-trip spread on names like Reliance, where NSE publishes an impact cost of 2-6 bps
and one tick is 0.95 bps. Roughly ten times reality.

The first run was worse still, at 53.8 bps, because of a mistake made *in the module
written to fix a mistake*: negative alphas were **dropped** rather than clamped to zero,
on the stated reasoning that clamping "would drag the median down". That is backwards.
34.2% of alphas are negative, so the estimator is mostly noise around a small true spread;
discarding the negative half and taking the median of the survivors returns about the 75th
percentile of that noise. Clamping to zero is Corwin & Schultz's own prescription.

Fixing it moved the estimate from 53.8 to 36.1 bps - still ten times too high. The
estimator degrades when volatility dwarfs the spread, and Indian equities average a **4.2%
daily range**. It does not work on this data. It is kept in the module and removed from
use, because a measurement that failed is worth recording.

**What caught both errors was the same thing: an implausible number.** Not a test. The
third time in this project - after [[Beating the median is not an edge]] and
[[The universe returned 84% a year]] - that a plausibility check found what tests did not.

## What replaced it: a bracket, not a point

The spread cannot be pinned from daily EOD data. So the cost is stated as a range:

| Bucket | optimistic (1 tick + size) | pessimistic (the old table) | round trip |
|---|---:|---:|---|
| mega | 0.97 bps | 5.0 bps | 22.8 - 30.9 |
| large | 1.29 bps | 10.0 bps | 23.5 - 40.9 |
| mid | 2.56 bps | 25.0 bps | 26.0 - 70.9 |
| small | 8.86 bps | 60.0 bps | 38.6 - 140.9 |
| micro | 105.46 bps | 150.0 bps | 231.8 - 320.9 |

**The gate uses the pessimistic bound**, so nothing is ever promoted on a hopeful
assumption. The optimistic bound is reported beside it, which makes visible exactly which
results are decided by cost uncertainty rather than by evidence - the thing a single
hardcoded number was hiding.

The original constants were not deleted. They became the worst case, which is the only
honest way to revise a number that decides outcomes.

## And then the answer arrived anyway

The bracket suggested `above_ma200` would net positive on mega-caps under *both* bounds.
That was arithmetic, not a result - it swapped the cost bucket while keeping a gross
excess measured across the whole universe. So it was pre-registered as [[h11]] and tested.

    above_ma200, min_adv Rs 50cr, 'large' pessimistic cost
    REJECT   t +1.80 (was +3.69)   IC +0.0236 (was +0.0347)   net -0.102%

The signal roughly **halved** on liquid names. The pre-registered prior said it would:
trend is stronger in smaller names, and the restriction removes exactly those. Capacity
passed, so this is not a technicality.

**Trend is real and lives where it cannot be harvested.** The cost estimate was too
pessimistic, and correcting it did not save the strategy - which is the outcome that makes
the correction credible rather than convenient.

## What is left

Not the universe, and no longer the cost estimate. **Turnover.** Both versions of this
test ran at 70-77% a period, costing 0.31-0.50% a period whichever bound is used. The
golden cross runs the same economic claim at 0.78% a session and nets positive. A
low-turnover construction of slow trend is the one thing about it still untested under
pre-registration.

Related: [[Trend is the only thing that has cleared significance]] · [[Why nothing passes]]
· [[h11]] · [[Alpha Validation Firewall]]
