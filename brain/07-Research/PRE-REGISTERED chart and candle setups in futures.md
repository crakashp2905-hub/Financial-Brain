---
type: research
tags:
  - research
  - pre-registration
  - technical
  - candles
  - fno
date: 2026-10-13
status: pre-registered
---

# PRE-REGISTERED chart and candle setups in futures

Written on 2026-10-13 under [[ADR-0004 one evidence standard]], before anything below is computed. Trial 7 of
2026Q4's budget - **one trial for the whole family**, with the multiplicity stated below. The owner asked for
chart- and candlestick-based edges.

## Why futures, and why five sessions

[[Four edges that replicate, and the chart patterns that do work]] measured candles as matched events and found the
directions a chart trader would state, strongest at five sessions: bullish marubozu +0.95%, bearish engulfing
-0.62%, shooting star -0.47%, hammer +0.39%. They were set aside because an 80 bp delivery round trip eats them;
that note called this test "the obvious next experiment". A stock future costs about 10 bp a round trip.

**Seen before**: those four candle effects were measured over the whole 2016-2026 period in the 500 most liquid
names, so for them G3 is partly a re-measurement; G5 (live) confirms. The other thirteen setups were measured only as
20-session cross-sectional ICs in the deep feature library, never as 5-session events in futures.

## The family, fixed (17 setups, side as a chart trader states it)

From `features/library.compute` on adjusted daily bars:

| long | short |
|---|---|
| bullish marubozu (`c_marubozu` > 0) | bearish engulfing (`c_bear_engulf`) |
| hammer (`c_hammer`) | shooting star (`c_shooting_star`) |
| bullish engulfing (`c_bull_engulf`) | evening star (`c_evening_star`) |
| morning star (`c_morning_star`) | liquidity sweep of the highs (`sweep_bear`) |
| liquidity sweep of the lows (`sweep_bull`) | break of structure down (`bos_down`) |
| break of structure up (`bos_up`) | bearish fair-value-gap retest (`fvg_bear_retest`) |
| bullish fair-value-gap retest (`fvg_bull_retest`) | gap down, unfilled (`gap_down_unfilled`) |
| gap up, unfilled (`gap_up_unfilled`) | 55-session closing low on volume >= 1.5x its 20-session mean |
| 55-session closing high on volume >= 1.5x its 20-session mean | |

## The trade, fixed

* **Names**: F&O stocks with a live near-month contract on the signal session (`data/basis_contracts.parquet`).
* **Timing**: the setup completes at the close of session *t*; enter at the close of *t+1* (no bid-ask bounce,
  no look-ahead), exit at the close of *t+6* - five sessions.
* **One event per name and setup per five sessions** (the first; overlapping repeats are dropped).
* **Outcome**: side x (stock return - equal-weighted return of all F&O names over the same window) - **20 bp**
  (10 bp the stock future, 10 bp a basket of index or stock futures as the hedge). The hedge is the traded universe,
  not the Nifty - batch three found every Nifty-hedged F&O book pays the universe's outperformance of the index.
* **Period series**: events averaged by entry month.

## The bar

A setup passes if G3 (|t| >= 2, right sign, in 2016-2021 **and** 2022-2026) and G4 (above the 95th percentile of
200 draws of random F&O names on the same entry dates, same side and window). Under the null a single setup passes
G3 with probability about 2 x 0.023^2 = 1e-3; across 17, the chance any passes by luck is about 2%.

## Predictions

* **K1** the four candle patterns keep their directions in both halves, gross.
* **K2** at most two setups pass; bullish marubozu is the likeliest.
* **K3** the smart-money setups (sweeps, BOS, FVG retests) fail - they had no 20-session IC.

Every setup goes into the paper zoo with its verdict.

Information, not advice.
