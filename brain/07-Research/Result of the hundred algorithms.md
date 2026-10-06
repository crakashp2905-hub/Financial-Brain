---
type: research
tags:
  - research
  - result
  - execution
  - arbitrage
  - options
date: 2026-10-07
status: result
---

# Result of the hundred algorithms

Pre-registration: [[PRE-REGISTERED the hundred algorithms]]. Of the 100, 5 were refused as market manipulation, 11
have no venue or market in India, and 19 have no data to test them with (all listed there with what would make them
testable). The rest are built and tested below. Nothing here places an order.

## Group 1 - execution algorithms (simulated on Kite minute bars)

60 names across the liquidity range; parent buy orders at 09:30 on every 10th session, finished by 15:15; 3,450
training orders (to 2025-08) and **4,818 test orders** (2025-09 onwards), at 0.5%, 2% and 10% of average daily volume.
Implementation shortfall against the 09:30 arrival price, test orders, bp (lower is better):

| algorithm | 0.5% ADV | 2% ADV | 10% ADV |
|---|---|---|---|
| immediate (takes up to 25% of each minute's volume) | 10.1 | 9.1 | **22.7** |
| Implementation Shortfall (Almgren-Chriss) | 7.2 | 8.4 | 23.0 |
| IS, patient | 5.9 | 7.2 | 24.1 |
| VWAP (20-session curve) | 6.1 | 7.4 | 26.0 |
| TWAP | 5.8 | 7.4 | 47.2 |
| POV 10% | 8.3 | 11.0 | 201 |
| queue-jump (limit a tick above the bid) | **4.0** | 11.3 | 257 |
| midpoint peg | 5.8 | 51.3 | 360 |
| primary peg / iceberg | 14.7 / 31.0 | 83 / 87 | 450 / 451 |
| market-on-close (last 30 minutes) | 11.2 | 53.1 | 478 |
| genetic-optimised schedule (learned) | 6.3 | **7.0** | 69.6 |
| K-means selector (learned) | 6.0 | 7.3 | 23.4 |
| random-forest selector (learned) | 8.4 | 12.5 | 26.7 |
| Q-learning (learned) | 8.9 | 15.1 | 166 |
| Bayesian pegger (learned) | 10.0 | 52.8 | 399 |

The full table is in `data/exec_algos_test.parquet`.

* **For small orders, patience pays a little:** at 0.5% of daily volume, a limit one tick above the bid (4.0 bp) or a
  TWAP/VWAP (about 6 bp) beat taking liquidity at once (10.1 bp).
* **For large orders it is the reverse.** At 10% of daily volume, everything that waits - TWAP, POV, every peg,
  market-on-close - is far worse: unfilled shares must be bought at the end of the day in one go, and the model charges
  that honestly. Fast, front-loaded execution (immediate, Implementation Shortfall, the K-means selector) is cheapest
  at about 23 bp.
* **X1** (immediate within 3 bp of the best at 0.5%): **FAIL** - 6 bp worse than the queue-jump limit.
  **X2** (schedules beat immediate by 15 bp at 10%): **FAIL** - immediate was the best. Note "immediate" here already
  spreads across the first minutes (25% of each minute's volume), so it is a fast schedule, not a single print.
  **X3** (adverse selection in pegs): **inconclusive** - the measure as designed mixes the cost of completing unfilled
  orders into the post-fill drift. **X4** (no learned algorithm beats the best fixed one by 2 bp): **PASS** - the
  genetic schedule leads at 2% by 0.2 bp; elsewhere a fixed algorithm wins.
* **Not faithful, stated:** market-on-open could not be tested properly - the NSE pre-open call auction (09:00-09:08),
  where opening volume really trades, is not in the minute bars, so the simulated version is meaningless. The real
  closing session (orders at the closing price, 15:40-16:00) is not modelled either.

**What the brain takes from it:** this system's orders are small (a Rs 10 lakh book trades well under 0.5% of a liquid
name's daily volume), so the trader view's execution plan - a limit inside the spread, not chasing - is the right one;
if orders ever grow to several percent of daily volume, front-load them.

## Group 2 - technical rules as trading systems (2016-2026, the 500 most liquid names, costs on every change)

Excess return a year over the equal-weighted universe they chose from (t on monthly excess):

| rule | all years | held-out 2022-26 |
|---|---|---|
| MA cross 50/200 | +1.6% (1.2) | +0.9% (0.6) |
| EMA ribbon | +0.1% (0.1) | -2.0% (-0.9) |
| Donchian 55/20 | -1.3% (-0.8) | -0.9% (-0.4) |
| Ichimoku breakout | -3.2% (-1.2) | -3.8% (-1.8) |
| CCI trend | -7.9% (-3.3) | -8.1% (-3.0) |
| MACD cross | -10.7% (-7.1) | -14.0% (-8.4) |
| stochastic cross | -11.2% (-8.4) | -11.3% (-6.3) |
| volume-spike entry | -14.7% (-9.1) | -14.9% (-6.6) |
| ATR breakout | -15.7% (-5.1) | -15.8% (-4.6) |
| RSI exhaustion | -18.3% (-9.7) | -16.1% (-5.8) |
| Bollinger reversion | -20.7% (-8.8) | -19.2% (-5.4) |

**R1** (no rule beats the universe after costs in 2022-26 with t >= 2): **PASS.** The slow trend rules are roughly
neutral; every faster rule is destroyed by trading costs.

Stops on the momentum book (top decile of 500, held 20 sessions, excess over the universe per period): no stop
+0.51% (t 1.7); a 15% trailing stop +0.33% (t 1.0); a 10% stop-limit +0.50% (t 1.8). An earlier run showed the
stop-limit at +0.87% - a bug in this note's script, which floored the loss at the limit price; a real stop-limit does
not fill when the price gaps through its limit, and corrected it adds nothing. **Stops do not help this book.**

Nifty 50, 2015-2026: buy-and-hold +9.2% a year (max drawdown 38%); scale-trader +1.2% (5%); grid +2.7% (35%); VIX mean
reversion +1.3% (35%) - the Nifty's daily return was +3.5 bp when VIX was in its top decile against +4.2 bp otherwise.
**Martingale was ruined in 93% of 500 shuffled two-year paths**; anti-martingale in none. **R2: PASS.**

Circuit bands: names closing at a 10%+ upper limit outperformed by +3.2% the next session (t 18); at a lower limit,
-4.9% (t -16). **This is not a tradeable edge**: a stock locked at its upper circuit has buyers queued and no sellers,
so the close cannot be bought; the measured return belongs to the queue. It is recorded as a warning (do not short
into a lower circuit, do not expect to buy an upper one), not a strategy.

## Group 3 - spreads and arbitrage (F&O names, theoretically long-short)

| strategy | all years, net (t) | held-out 2022-26, net (t) |
|---|---|---|
| pairs by distance (per 6-month window) | +0.23% (0.9) | +0.68% (1.8) |
| pairs by cointegration (20 pairs a window) | +0.95% (1.4) | -0.08% (-0.1) |
| PCA statistical arbitrage (per week) | +0.02% (0.8) | -0.02% (-0.8) |

**A1** (positive gross, under 0.1% a week net, no t >= 2 in 2022-26): **PASS** for all three.

NSE vs BSE closing prices: median gap 9.6 bp; **20% of liquid name-days differ by more than the 25 bp it would cost
to cross.** **A2** (under 1%) **FAIL as numbers** - but both closes are computed averages of the last half hour, not
prices anyone could trade at simultaneously; testing real cross-exchange arbitrage needs simultaneous intraday quotes
from both exchanges, and only NSE's are recorded. Not an edge until that test exists.

Cash-and-carry, reverse, index arbitrage (#45, #48, #49, #55): **pending** - they need Kite's continuous-futures history,
reachable only with the day's login; the code path (`kite_data.candles(continuous=True)`) is ready.

## Group 5 - options on the Nifty, synthetic (Black-Scholes at India VIX, no skew)

Monthly cycles 2015-2026, P&L as a share of the Nifty, after 1% slippage a leg-side and STT:

| strategy | mean | t | win rate | worst cycle |
|---|---|---|---|---|
| short straddle | +0.39% | 1.3 | 64% | -23.8% |
| short strangle (5%) | +0.19% | 0.9 | 77% | -21.6% |
| iron condor (5%/8%) | +0.07% | 0.9 | 77% | -2.8% |
| delta-hedged long straddle | **-0.82%** | -9.0 | 18% | -3.2% |
| weekly rolling straddle (per week) | +0.18% | 3.1 | 63% | -12.1% |

**O1: PASS** - selling volatility wins small and often and loses large (skew -2.6 to -5.0); buying it and hedging
loses steadily, because India VIX has usually been above the volatility the Nifty then delivered. **O2** stands: these
are synthetic upper bounds (no skew, a 30-day volatility index used for weekly options), and none is used by the brain
until a real options history exists.

## Group 4 - more learners (same panel, embargo, books and costs as the LightGBM challenger)

Net monthly excess over the 500 most liquid names, **2022-2026** (t), and the difference from plain momentum:

| learner | top 20 | vs momentum | top 50 | vs momentum |
|---|---|---|---|---|
| LightGBM (the qualified challenger) | **+0.77% (2.3)** | +0.29% (0.5) | **+0.59% (2.3)** | +0.06% (0.1) |
| plain momentum (champion) | +0.48% (0.9) | - | +0.53% (1.4) | - |
| LSTM (#72) | +0.40% (1.1) | -0.08% | +0.27% (1.0) | -0.26% |
| neural network (#68) | +0.20% (0.6) | -0.29% | +0.17% (0.6) | -0.36% |
| random forest (#69) | +0.13% (0.4) | -0.35% | +0.27% (1.1) | -0.26% |
| linear SVM (#71) | +0.07% (0.2) | -0.41% | +0.01% (0.1) | -0.52% |
| genetic feature weights (#70) | **-0.80% (-2.4)** | -1.29% | -0.58% (-1.9) | -1.11% |

Regime switching (#69 as a strategy selector), choosing each month between the momentum and the reversal book from
market features: 2022-26 **+0.41% a month against +0.46% for always holding momentum**; it chose momentum in 86% of
months. It learned "mostly momentum", which is what doing nothing already does.

**M1** (none beats LightGBM by more than 0.10% a month in 2022-26; none beats momentum with t >= 2): **PASS** - every
other learner is behind LightGBM, and the genetic search, which optimised the training years' IC directly, is the worst
of all: the clearest case of a fit to the past that does not carry forward.
