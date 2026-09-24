---
type: research
tags:
  - research
  - strategy
  - firewall
date: 2026-09-24
---

# Trend is the only thing that has cleared significance

After 57 trials, one signal has cleared the Bonferroni gate. It was already in the
codebase, and it had never been tested.

## above_ma200

    above_ma200   REJECT   trial 50   IC +0.0347   t +3.69   net/period -0.26%   turn 70%

At 50 trials the bar is |t| > 3.29. This scores **3.69**, with raw p = 2.2e-04 and
p x 50 = **0.0112 < 0.05**. It is the first and only signal in this project to pass
`significance`.

It still REJECTS, on costs. Gross excess is **+0.24% per period**; 70% turnover at a 0.71%
round trip costs **0.50%**, netting -0.26%.

## It had been sitting there since the first feature build

`above_ma50` and `above_ma200` were computed into the `features` table from the beginning
and never added to `benchmark.FEATURES`, so they were never tested. The reason is now
visible: they are BOOLEAN, and the benchmark multiplies the feature by its direction.
`boolean * integer` is an error in DuckDB, so **the first person to try would have hit a
type error and moved on**. A cast fixes it.

The most significant signal in the project was excluded by an unhandled type.

## It is one half of a result

The other half arrived the day before, from a different family of test:

| | t | net | turnover |
|---|---:|---:|---:|
| `above_ma200` (cross-sectional quintile) | **+3.69** | -0.26%/period | 70%/period |
| `ma_double_cross_50_200` (time-series hold) | +2.74 | **+0.0195%/session** | 0.78%/session |

**`above_ma200` clears significance and dies on costs. The golden cross survives costs and
misses significance.** They are the same economic claim - slow trend in Indian equities -
measured two ways, and each passes the gate the other fails.

That is the strongest thing this project has found, and it is still not a finding. Neither
cleared the whole firewall, and two near-misses are not one pass.

## What must happen next, and what must not

**Must not:** search the neighbourhood. 40/180, 60/220, a weekly rebalance, an ADX filter
on top, a volatility overlay - tried until something clears 3.3. Every attempt raises the
bar for everything after it, and this is exactly the behaviour the trial counter exists to
punish. The temptation is much stronger now than it was at t = -0.45, which is precisely
when the rule matters.

**Must:** one pre-registered test, stated in full before it runs, of a **low-turnover
implementation of the 200-day trend filter** - the obvious candidate being a rebalance
band or a monthly check, since the gap between 70% and 0.78% turnover is the entire
difference between the two results above. One trial, one statement of what would count as
success, written down first.

If that fails, slow trend is closed here and the honest summary is that this universe
offers nothing harvestable at these costs.

Related: [[The golden cross and the bar it did not clear]] ·
[[Charts, candles and indicators]] · [[Alpha Validation Firewall]] · [[MOC Strategies]]
