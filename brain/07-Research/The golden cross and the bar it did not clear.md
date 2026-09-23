---
type: research
tags:
  - research
  - strategy
  - firewall
date: 2026-09-23
---

# The golden cross and the bar it did not clear

The closest thing to a signal this project has found, and a clean demonstration of why
that sentence is not the same as finding one.

Five time-series strategies from `letianzj/QuantResearch` (MIT), run through
[[Time-series strategies]] on 2016-2026 Indian equities, scored as daily excess over the
equal-weighted liquid universe, net of costs:

| Strategy | t | net/session | turnover | verdict |
|---|---:|---:|---:|---|
| ma_cross_200 | -3.82 | -0.0223% | 3.75% | REJECT |
| **ma_double_cross_50_200** | **+2.74** | **+0.0195%** | 0.78% | REJECT |
| faber_taa_10m | +1.38 | +0.0091% | 0.89% | REJECT |
| bollinger_reversion_20_2 | -6.75 | -0.0599% | 8.07% | REJECT |
| turtle_20_10 | -5.20 | -0.0445% | 3.85% | REJECT |

## The one that nearly worked

The golden cross - hold while the 50-session average is above the 200 - returned **+5.0% a
year in excess of the universe, after costs**, at t = +2.74. Raw p = **0.0061**.

A write-up that stopped there would say "highly significant, p < 0.01". Corrected for the
**35 trials** this project has run, p = **0.215**, and the bar is |t| > 3.19. It fails.

That gap - 0.0061 against 0.215 - is the entire argument for the
[[Alpha Validation Firewall]] in one line. The strategy was not cherry-picked; it came
from a published list and was tested once. The correction still refuses it, because 35
looks at the data is 35 looks whether or not any single one was innocent.

## The mechanism is coherent, which is why it is worth remembering

The two slow, smooth filters are positive; the three that whipsaw are negative. The
decisive number is turnover:

    ma_double_cross_50_200   0.78%/session   two smoothed legs
    ma_cross_200             3.75%/session   price against one smoothed leg

Comparing a noisy price to an average makes the signal cross constantly; comparing two
averages does not. A five-fold turnover difference between two versions of "follow the
trend" is the whole result, and it agrees with [[Low volatility]] and
[[Short-term reversal]], both of which died at the cost gate rather than the signal.

## What must not happen next

The tempting move is to search near it - 40/180, 60/220, a weekly rebalance, a volatility
filter - until something clears 3.19. That is precisely what the trial counter exists to
punish, and each attempt raises the bar for every strategy that follows.

The honest continuation is a **pre-registered out-of-sample test**: state the parameters
before looking, on a period or universe not used here, and count it as one trial. Until
that is done this is a promising observation, not a finding.

Related: [[Time-series strategies]] · [[Alpha Validation Firewall]] · [[MOC Strategies]] ·
[[The universe returned 84% a year]]
