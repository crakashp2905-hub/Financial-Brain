---
type: research
tags:
  - research
  - pre-registration
  - intraday
  - regime
date: 2026-10-06
status: pre-registered
---

# PRE-REGISTERED trading with the market trend

Written before computing, at the owner's request: *trade with the market, not against it - going against the
market is where the losses come from.* The prompt was the week of 2026-09-28: in a falling market 120 long
breakouts lost Rs 86,430 gross while 177 shorts made Rs 1,636.

"Take only the trades that will be profitable" cannot be built - no rule knows a trade's outcome before it is
placed. What can be built and tested is a filter on information available **at the moment of the decision** that
removes the trades fighting the market.

## Filters (applied to the opening-range breakout exactly as in live-v1: full-range stop, 2R target, 15:15 exit)

* **D - daily trend.** Nifty 500's previous close against its 20-session average at that close. Above: long
  breakouts only. Below: short breakouts only.
* **I - intraday market direction.** At the trigger minute, the equal-weighted return of the ~300 minute-bar names
  from their 09:15 open to that minute's close (only bars up to the trigger). Up: longs only. Down: shorts only.
* **B - both.** A trade is taken only when D and I agree with its side.

Unfiltered ORB is the comparison.

## Test

Kite minute bars, ~300 names, 2024-09 to 2026-10, the same simulation and costs as
[[Result of the opening-range breakout with stops]]. **Train: sessions before 2025-10-01. Test: from 2025-10-01.**
The filters are fixed above, so "train" only describes; the verdict is on the test year. Per-trade net return,
standard errors clustered by day (trades on one day share one market). Running Bonferroni bar 3.79.

A filter **qualifies for paper trading as live-v2** if, in the test period, net per trade is **positive** with
day-clustered t >= 2. It **would deserve real attention** only if it also clears the Bonferroni bar.

## Predictions

* **T1** Every filter raises gross per trade over unfiltered ORB, in train and test: trend alignment is the
  best-documented property of breakouts.
* **T2** The improvement is 5-15 bp a trade gross: not enough to cover the 14-24 bp round trip, so **no filter
  qualifies** (net stays negative in the test year).
* **T3** B (both) has the highest gross per trade but the fewest trades.

## Swing, the same idea (descriptive, in-sample)

On the existing swing-v1 replay (5,607 trades, long only): split trades by whether Nifty 500 was above its
50-session average on the signal day. Prediction **T4**: trades signalled in an up-trend earn more in absolute
terms, but their excess over the universe is no better - a long-only filter avoids falling markets, it does not
create stock-picking skill.
