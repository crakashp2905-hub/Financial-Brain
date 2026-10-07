---
type: research
tags:
  - research
  - pre-registration
  - paper-trading
date: 2026-10-07
status: pre-registered
---

# PRE-REGISTERED live paper zoo

Written before it runs. The owner asked that live paper trading include every algorithm that was built. Every tested
strategy with a daily rule now runs on paper each evening, **with its rules frozen exactly as tested** and its test
verdict beside its live record - so the live record can confirm or contradict each result on data no test saw.

## What runs (from the 2026-10-07 close)

* **Eleven technical rules** (MA cross 50/200, EMA ribbon, MACD cross, Bollinger reversion, RSI exhaustion,
  stochastic cross, ATR breakout, Ichimoku breakout, Donchian 55/20, CCI trend, volume-spike entry): long or flat per
  name among the 500 most liquid, equal-weighted, as in [[Result of the hundred algorithms]].
* **Pairs by distance and by cointegration, PCA statistical arbitrage**: F&O names, long-short (the short leg priced
  as a stock future), pairs re-formed every 125 sessions from the previous 250.
* **Nifty 50 schemes**: VIX mean reversion, scale-trader, grid, martingale and anti-martingale (martingale is run to
  show its ruin, not as a candidate).
* Already running as books: ranking v1 (top 5), swing (top 50), the learned model (top 20), momentum (top 20) - and
  now **ranking v2 (top 20)**.

## How it is scored

Each strategy holds target weights decided at a close; the next session's close-to-close return is earned on them;
one-way costs are charged on every change of weight (0.15% for delivery longs, 0.05% for a futures short or the index).
Long-only equity strategies are compared with the equal-weighted 500; long-short ones with zero; Nifty schemes with
the Nifty. **No verdict before 60 sessions**, and each strategy's live verdict is judged against its own pre-registered
test, not against today's P&L. Nothing in the zoo changes any rule; it only records.

## Predictions

* **Z1** After 60 sessions, the live ordering of the eleven technical rules matches their test ordering (slow trend
  rules near zero, fast rules losing to costs) - Spearman correlation above 0.5.
* **Z2** Martingale is ruined, or in a drawdown above 50%, within its first 250 sessions with probability above the
  shuffled-path estimate's 93% ... i.e. it is not ruined only if the Nifty avoids a losing streak.

## Addendum, 2026-10-07: the carry book (written before it runs)

After [[Result of cash-and-carry and basis trading]]: **carry_basis_10** joins the zoo. From each evening's F&O
snapshot (`fno_snapshots`: spot, near future, expiry, annualised basis), any F&O stock whose annualised basis exceeds
10% with at least 5 calendar days to expiry is entered (long spot, short the near future, equal weight, at most 10
names), held to expiry, P&L marked daily as the change in spot minus the change in the future, per rupee of spot, after
0.265% round-trip costs. Benchmark: the risk-free rate (6.5% a year). Prediction **Z3**: after 60 sessions its excess
over the risk-free rate is positive, as in the history (+0.14% a contract, about +1.7% a year).
