---
type: research
tags:
  - research
  - exit-rules
date: 2026-10-04
status: result
---

# Result of exit rules

Predictions committed at `1fd9e5e` before running. The book is the names the engine would own under
"everything except the worst 20% by composite", equal weight, 129 monthly sessions 2016-2026, ~1,200 names a
month, held 20 sessions. Baseline (hold to the rebalance): **+1.64% a month** with a **5th percentile of
-12.1%**. Stops are checked on daily closes and sell at the breach close, then hold cash at 6%.

    rule    mean vs hold      t     5th pct    tail vs hold    fired
    S8        -13 bp        -0.54    -9.8%      +2.3 pp         28%
    S12        -3 bp        -0.18   -11.5%      +0.5 pp         15%
    S15        -1 bp        -0.06   -12.1%      +0.0 pp          9%
    T10       -23 bp        -0.87   -10.3%      +1.8 pp         35%
    T15        -5 bp        -0.29   -11.8%      +0.3 pp         14%
    T20        +2 bp        +0.20   -12.2%      -0.1 pp          6%
    bar |t| >= 2.64 (six trials): none clears

    B1 every stop has a lower mean, tight stops lowest     PARTLY  five of six lower, T20 +2 bp; S8 and T10 are the lowest, as predicted
    B2 S8/S12/T10 cut the 5th pct by >=20% at >25 bp cost  FAIL    S8 cut it 19%, T10 15%, S12 4%; the mean cost was 13, 3 and 23 bp
    B3 no stop significantly improves the mean             PASS    best t is +0.20
    B4 S8 fires on >25% of positions                       PASS    28%

**Stops are nearly free and nearly useless.** The mean return given up is at most 23 bp a month and is never
distinguishable from zero. In exchange the worst-5% outcome improves by about 2 points for the tightest rules
(S8: -12.1% -> -9.8%) and by nothing for the wide ones. That is a real but small trade: a tight stop shaves
the left tail by a fifth for roughly the cost of one basis-point-sized drift.

**My prediction about the cost was too strong.** I expected short-horizon reversal to make stops sell near the
lows and cost 25 bp or more; it cost 13 bp for S8 and the difference is statistically nothing. The reversal
effect is real (its IC is +0.03 for one to five sessions) but it is too weak to dominate a 20-session path.

**What this does not say**
* Stops fill at the **close** of the breach day. A real stop order triggers intraday and fills at or below the
  trigger, and a stock that gaps through the stop fills at the gap, so realised stops are worse than this.
* Equal-weight, long-only, 20-session holds. A stop on a position held for a year is a different problem.
* A sold name is not re-bought until the next rebalance; a rule that re-enters would pay costs this one avoids.
* The costs of the exit itself are not charged here because the position is sold at the rebalance anyway; a
  strategy that would otherwise have held across the rebalance (a low-turnover book) pays 20 bp of STT it would
  not have paid.

**The one defensible use:** a wide stop (S15 or T20) as a catastrophe limit changes almost nothing and fires on
6-9% of positions; a tight one (S8, T10) is a mild left-tail trim you pay for in mean return. Neither adds
return.

Related: [[PRE-REGISTERED market timing and exit rules]] · [[Result of market timing]] · [[Why costs are bigger than the edge]]
