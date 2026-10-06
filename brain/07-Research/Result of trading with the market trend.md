---
type: research
tags:
  - research
  - result
  - intraday
  - regime
date: 2026-10-06
status: result
---

# Result of trading with the market trend

Pre-registration: [[PRE-REGISTERED trading with the market trend]]. Opening-range breakout exactly as live-v1
(full-range stop, 2R target, 15:15 exit), ~300 names, Kite minute bars 2024-09 to 2026-10, intraday costs by
bucket. Train before 2025-10-01, **test from 2025-10-01**. Per trade, bp; t clustered by day.

| period | filter | trades | share | gross bp | net bp | net t |
|---|---|---|---|---|---|---|
| train | none | 62,851 | 100% | +6.6 | -14.3 | -5.6 |
| train | D daily trend | 32,744 | 52% | +7.0 | -14.0 | -6.5 |
| train | **I market today** | 44,651 | 71% | **+12.6** | **-8.4** | -3.4 |
| train | B both | 24,272 | 39% | +12.6 | -8.5 | -4.9 |
| **test** | none | 60,520 | 100% | -0.1 | -21.2 | -10.5 |
| **test** | D daily trend | 30,204 | 50% | -4.2 | -25.3 | -8.6 |
| **test** | I market today | 40,189 | 66% | -0.1 | -21.3 | -6.6 |
| **test** | B both | 20,395 | 34% | -3.0 | -24.2 | -6.3 |

* **T1** (every filter raises gross, train and test) **FAIL.** In the training year, trading only with the day's
  market direction doubled gross (+6.6 to +12.6 bp). In the held-out year the improvement was **zero**, and the
  daily-trend filter made it worse.
* **T2** (no filter qualifies) **PASS.** Net is -21 to -25 bp a trade in the test year for every filter.
* **T3** (both has the highest gross) **FAIL** in the test year.

**No filter qualifies for paper trading as live-v2.**

## Swing (descriptive, in-sample)

swing-v1 replay split by Nifty 500 against its 50-session average on the signal day: down-trend signals 1,844 trades,
net +0.37%, excess -0.39%; up-trend 3,758 trades, net +0.20%, excess -0.37%. **T4 half wrong:** trades signalled in
an up-trend did *not* earn more; the excess is the same either way. A trend switch would not have rescued the book.

## What it means

The week of 2026-09-28 looked like a clear lesson - longs lost in a falling market - and the training year agreed.
The held-out year did not. "Go with the market" is true in hindsight on any given week, because the market's
direction for the *rest of the day* is what decides a breakout, and the direction known at 09:45 does not predict it
reliably enough to pay 15-25 bp of costs.

This is the reason the learning loop (`paper/loss_review.py`) only ever *proposes*: a rule that removes last week's
losers always looks right on last week, and this one would have looked right on a whole year, then failed on the
next.
