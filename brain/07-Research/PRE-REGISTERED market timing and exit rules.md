---
type: research
tags:
  - research
  - pre-registration
  - market-timing
  - exits
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED market timing and exit rules

Written and committed before either study runs. Answers "when to be in the market and when to get out",
which nothing in this project has tested at the index level.

## A. Index-level timing (Nifty 500 price index, 2015-2026)

Is a rule for being in or out of the index better than staying invested? Data: `index_levels_canonical`,
Nifty 500 PRICE, 2015-01-01 to 2026-09-18 (the same price index for the rule and for buy-and-hold, so
dividends cancel). Signal at the close of day t, position from the close of t+1 (one-day lag, no peeking).
Out of the market the money earns **6% a year** (assumption). Each switch costs **5 bp one way**
(assumption; sensitivity at 2.5 and 12.5).

Five rules, fixed here, nothing tuned:

    R1 trend       in when close > 200-day SMA
    R2 cross       in when 50-day SMA > 200-day SMA
    R3 breadth     in when the share of liquid names above their own 200-day average >= 50%   (sample from 2016-06)
    R4 volatility  in when 20-day realised vol <= 1.5 x its own trailing 250-day median
    R5 both        in when R1 and R3 both say in                                               (sample from 2016-06)

Judged by Sharpe over cash, CAGR, maximum drawdown and Calmar against buy-and-hold on the same dates. A rule
"beats" buy-and-hold only if a stationary block bootstrap (mean block 20 days, 5,000 draws) of the
Sharpe difference has its **1st percentile above zero** (five trials, so 99%, not 95%).

Predictions:
* **A1** R1 and R2 cut maximum drawdown by at least 25% relative to buy-and-hold, but their Sharpe
  difference is **not** significant at 99%.
* **A2** no rule has a higher CAGR than buy-and-hold: the lag, the whipsaws and cash at 6% against an
  equity index cost more than avoiding a handful of drawdowns saves.
* **A3** the breadth rule R3 gives no significant benefit.
* **A4** R4 reduces drawdown but exits late and re-enters late; Sharpe difference not significant.
* **A5** R1 switches at least four times a year and its cost drag is under 1% a year at 5 bp.
* Power caveat, stated first: about 11.7 years and one real crash (March 2020). A rule that clears the
  bar here is a hypothesis, not a finding; a rule that does not is "not detected", not "does not work".

## B. Exit rules on a stock book

Book: the names the engine would own under "everything except the worst 20% by composite", at each
monthly session (every 20th), 2016-2026, equal weight, held 20 sessions. Baseline: hold to the rebalance.
Each stop rule is checked on **daily closes** and, when it fires, sells at that day's close and holds cash
(6% a year) to the rebalance. The sell happens at the rebalance anyway, so costs are the same and are not
charged. Six rules, fixed here:

    S8 / S12 / S15   exit when the close is 8% / 12% / 15% below the entry close
    T10 / T15 / T20  exit when the close is 10% / 15% / 20% below the highest close since entry

Per-session mean (rule minus baseline) over names, t across the 129 sessions, six trials so the bar is
|t| >= 2.64.

Predictions:
* **B1** every stop rule has a **lower** mean 20-session return than holding, tight stops (S8, T10) the
  lowest: at one to twenty days these signals mean-revert, so stops sell near the lows.
* **B2** S8, S12 and T10 cut the 5th-percentile outcome by at least 20% relative, at a cost of more than
  25 bp a month in mean return.
* **B3** no stop rule has a significantly *positive* mean difference.
* **B4** S8 fires on more than 25% of positions within the 20 sessions.

Pass criteria as written. No rule, threshold, window, lag or cost is changed after seeing a result.
