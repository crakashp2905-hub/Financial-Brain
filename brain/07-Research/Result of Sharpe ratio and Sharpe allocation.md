---
type: research
tags:
  - research
  - result
  - sharpe
  - allocation
date: 2026-10-07
status: result
---

# Result of Sharpe ratio and Sharpe allocation

Tests [[PRE-REGISTERED Sharpe ratio and Sharpe allocation]]. Historical paper record: each strategy's tested monthly
series, net of its own costs, total return; Sharpe above 6.5% a year, annualised from monthly. Panel saved as
`data/strategy_monthly_panel.parquet`, the allocation as `data/sharpe_allocation.parquet`.

## The record (net Sharpe above cash)

| Strategy | Sharpe 2016-26 | Sharpe 2022-26 | Return a year 2022-26 | PSR 2022-26 | DSR 2022-26 |
|---|---:|---:|---:|---:|---:|
| carry: basis > 10% | +0.68 | **+1.68** | 7.7% | 1.00 | 0.91 |
| learned model top 20 (2019+) | **+0.91** | +0.88 | 24.4% | 0.97 | 0.29 |
| pairs by distance (long-short) | +0.11 | +0.65 | 8.4% | 0.93 | 0.13 |
| rule: ma_cross_50_200 | +0.48 | +0.54 | 15.9% | 0.87 | 0.10 |
| equal-weight 500 (the market) | +0.38 | +0.50 | 15.1% | 0.85 | 0.09 |
| ranking v2 top 20 | +0.82 | +0.44 | 14.6% | 0.82 | 0.07 |
| rule: donchian_55_20 | +0.35 | +0.43 | 13.6% | 0.81 | 0.07 |
| rule: ema_ribbon | +0.45 | +0.39 | 12.8% | 0.80 | 0.05 |
| ranking v1 top 20 | +0.09 | +0.33 | 11.5% | 0.75 | 0.05 |
| nifty: VIX mean reversion | +0.01 | +0.31 | 9.3% | 0.75 | 0.04 |
| rule: ichimoku_breakout | +0.28 | +0.27 | 10.3% | 0.72 | 0.03 |
| momentum top 20 (champion) | +0.64 | +0.22 | 9.4% | 0.68 | 0.03 |
| nifty 50 buy-and-hold | +0.33 | +0.11 | 7.3% | 0.59 | 0.01 |
| nifty: grid | +0.08 | +0.10 | 7.1% | 0.58 | 0.01 |
| rule: cci_trend | +0.07 | +0.05 | 6.0% | 0.54 | 0.01 |
| rule: stochastic_cross, macd_cross, pca stat-arb, volume_spike, atr_breakout, rsi_exhaustion, bollinger_reversion | | -0.12 to -0.61 | | < 0.41 | 0.00 |
| nifty: scale-trader | +0.39 | -0.84 | 6.6% | 0.03 | 0.00 |
| intraday ORB (2024+) | | -8.99 | -35% | 0.00 | 0.00 |

DSR here uses the spread of Sharpe across the strategies **without ORB** for the luck benchmark (expected best of 330
by luck: 1.15 annualised). The run as first computed included ORB, whose -9 Sharpe inflated that spread five-fold and
put every DSR at 0.00; the trial-spread estimate was not fixed in the pre-registration, so both are reported. The
conclusion (S2) is the same either way.

## Predictions

* **S1 - fails in part.** Carry has the highest Sharpe in 2022-26 (1.68) and the learned model the highest among
  stock-selection strategies in both periods - as predicted. But "every equity long-only Sharpe under 0.8" is wrong:
  the learned model is 0.91 (2016-26) and 0.88 (2022-26), ranking v2 0.82 over 2016-26 (its 2016-21 half is in-sample).
* **S2 - holds.** No deflated Sharpe reaches 0.95. Carry 2022-26 comes closest (0.91): its 1.68 is mostly the very low
  volatility of a hedged position earning just above cash (7.7% vs 6.5%), and a 0.4%-a-month vol makes a small edge look
  like a large Sharpe. The learned model's 0.88 deflates to 0.29.
* **S3 - fails.** Sharpe allocation 2022-26: **+11.4% a year, Sharpe 0.42, max drawdown 14%**, against equal weight
  +7.7% (Sharpe 0.14, drawdown 17%) and momentum alone +9.4% (0.22, 30%). It beat equal weight by 3.7% a year, not
  under 1%, and momentum by 2.1%, just over the 2% bound. Past Sharpe persisted more than predicted - mostly by keeping
  money out of the rules that lose (the reversion rules get no weight) and in carry and the learned model.
  Its own Sharpe after costs (0.42, PSR 0.81) is still well under carry's alone; it is a better fund-of-strategies than
  equal weight, not a strong strategy.
* **S4 - condition met.** Allocation beat equal weight in **all five** years (2022 -1.9% vs -4.5%, 2023 +38.8% vs
  +31.2%, 2024 +19.0% vs +14.9%, 2025 -2.4% vs -4.4%, 2026 to Aug +4.8% vs +2.8%). So:
  - a **sharpe_allocator** book joins the live paper zoo (`paper/zoo.py`): each month, weights proportional to the
    trailing 36-month Sharpe of each zoo strategy (positive only), from the historical panel extended by the zoo's own
    complete live months; it earns its weights times their nets, cash interest on what it leaves idle, and 0.3% on
    the share moved at each rebalance. Benchmark: equal weight across the same strategies.
  - Sharpe is reported everywhere: the zoo (from 20 sessions), the 18:30 report, the terminal's zoo table, and the
    evidence tiers (2022-26 Sharpe with PSR and DSR).

## Deviation, stated

The tested allocation also held the stock-selection strategies (learned 10%, ranking v2 8%, momentum 6%, ranking v1 3%
on average). Those run as separate paper books with trade-level records, not daily series, so the live allocator
splits only over the zoo's strategies (October 2026 weights: carry 20%, pairs 19%, VIX mean reversion 14%,
ma_cross 11%, donchian 10%, ema_ribbon 9%, PCA 6%, ichimoku 5%, grid 5%, cci 2%). Its live record is therefore a
narrower version of what was tested, and is judged as such.

Information, not advice.
