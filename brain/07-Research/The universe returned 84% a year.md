---
type: research
tags:
  - research
  - mistake
  - backtest
date: 2026-09-23
---

# The universe returned 84% a year

The [[Time-series strategies]] harness shipped with two bugs. Both were caught by one
sanity check - printing the benchmark's own return and asking whether it was possible.

    universe mean daily +0.2565%  ->  +89.8% annualised

Indian equities did not return 90% a year. Everything computed against that benchmark was
meaningless, and all five strategies had already been scored against it.

## Bug one: a look-ahead in the filter, not the signal

The universe was filtered to names trading at least Rs 1 crore **on the bar whose return
was being measured**. Volume spikes with price, so conditioning on today's turnover
selects the sessions a name moved hard.

| Liquidity judged on | Universe return |
|---|---|
| the current bar's turnover | +0.244%/day → **+84%/yr** |
| turnover already observed when the position formed | +0.070%/day → +19%/yr |

The signal itself was correctly lagged. Care had gone into the obvious leak - not filling
at the close that produced the signal - and none into the *selection*. A backtest can
look ahead through which rows it keeps as easily as through which values it reads.

## Bug two: impossible returns

The raw panel holds a **+3750%** session and 146 sessions above +100%. Indian equities
trade under circuit limits of 5-20% a day, so these are splits and bonuses the archive
failed to adjust, not market moves. One of them distorts an equal-weighted average of a
few hundred names. Moves beyond 50% are now excluded on that reasoning.

## Why it mattered, concretely

Fixing the benchmark **flipped a sign**:

    ma_double_cross_50_200    broken: t = -3.27      fixed: t = +2.74

A bug in the benchmark does not merely add noise - it can invert a conclusion. The broken
run would have recorded the golden cross as a clear failure and closed the question.

## The check that caught it

Not a test. A print statement asking whether the benchmark's own number was physically
possible. The same check that caught [[Beating the median is not an edge]] - *compare the
number to the world before using it* - and the same lesson arriving a third time, after
the Laya latency figure. Both bugs now have tests.

The five broken runs stay in the trial ledger. They were looks at the data, and a look
does not stop counting because the code was wrong.

Related: [[Time-series strategies]] · [[Beating the median is not an edge]] ·
[[The golden cross and the bar it did not clear]] · [[Alpha Validation Firewall]]
