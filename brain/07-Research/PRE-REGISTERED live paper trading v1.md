---
type: research
tags:
  - research
  - pre-registration
  - paper-trading
  - live
date: 2026-10-05
status: pre-registered
---

# PRE-REGISTERED live paper trading v1

Written and committed before the first live or replayed paper trade. The testing phase the owner asked for:
look for trades through every session, paper-trade as many as the rules allow, every trading day, and learn
from the record.

## What runs

`paper/intraday.py` (engine) and `paper/intraday_runner.py` (live and replay), version **live-v1**, on the 200
most liquid NSE names, polling Kite's read-only quote endpoint every 20 seconds from 09:15 to 15:30 IST. Paper
account Rs 10 lakh; each trade risks 0.25% of it to the stop, at most 10% of the account in one trade, and no
new trade once open intraday exposure reaches 5x the account. Full intraday costs (STT on the sell, brokerage,
exchange, SEBI, stamp, GST) plus impact by liquidity bucket. **No order is placed; no order code exists.**

    ORB  opening-range breakout, long and short; range 09:15-09:29; entries 09:30-11:00; stop at the other end
         of the range; target 2R; flat at 15:15
    GAP  gap fade; at 09:30, gap >= 1.5% and back below the open -> short (mirror for a gap down); stop at the
         day's extreme so far; target the previous close; flat at 15:15

Live days are the evidence. Replays of past days (exact Kite minute candles) are labelled `replay` and kept
apart: the rules are fixed in advance, but a replay is not a decision taken before the price existed.

## How it learns, and how it does not

The record accumulates per strategy: net bp per trade, its standard error, mean R, win rate. It is compared to
what the backtest said (ORB: about -18 bp a trade net). Statuses: COLLECTING (under 100 trades),
DIFFERS_FROM_BACKTEST, LOSING, WINNING, NO_EDGE_DETECTED.

**The rules do not change with the daily results.** Tuning a stop or a threshold after a good or bad day is
fitting noise: one day of 200 names is a single draw of the market, not 200 independent tests. A change is a
new version (`live-v2`), pre-registered with its reason, and the record of v1 is kept, not overwritten.
Mistakes found in the *code* (a bad fill, a wrong cost, a crash) are fixed immediately and noted, because those
are errors, not parameters.

## Predictions

* **L1** ORB live net is negative and within 2 standard errors of the backtest's -18 bp a trade once 100 trades
  have closed.
* **L2** GAP, untested before, is negative net (a 1.5% gap fade earns less than its ~15-25 bp round trip).
* **L3** win rates are under 45% for ORB and under 50% for GAP.
* **L4** the 20-second polled live fills are worse than the replay's minute-bar fills (live stops overshoot).

Pass criteria as written.
