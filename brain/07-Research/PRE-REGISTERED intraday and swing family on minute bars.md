---
type: research
tags:
  - research
  - pre-registration
  - intraday
  - swing
date: 2026-10-15
status: pre-registered
---

# PRE-REGISTERED intraday and swing family on minute bars

Written on 2026-10-15 under [[ADR-0004 one evidence standard]] (amendment 3: the owner said "do all your
recommendations", and the recommendation was to run this family when the minute history lands), **before the new
minute history (2016-2026, `data/minute_history.duckdb`, still downloading) has been read for anything**. One trial
for the family of nine.

## Why, and the limits stated first

The owner asked for edges in second, minute and hour bars. Seconds: Kite serves no history below one minute, so there
is nothing to backtest. Minutes and hours: earlier tests here found no gross edge in index direction and small gross
stock signals eaten by ~13.5 bp intraday cash costs - on two years of minute bars. Ten years in stock **futures**
(~10 bp a round trip) is the case not yet tested. Survivorship: Kite lists only current instruments, so names
delisted since 2016 are missing - a bias toward survivors, stated.

## Universe and costs

* F&O stocks with a live near-month contract on the day (`data/basis_contracts.parquet`), traded as stock futures,
  priced from the stock's one-minute bars (a proxy for the future, intraday).
* Intraday single-leg trades: 10 bp a round trip. Long-short pairs: 10 bp a leg. Swing trades (held overnight):
  hedged with the equal-weighted F&O basket, 20 bp (as [[PRE-REGISTERED chart and candle setups in futures]]).
* Entries happen at the close of the bar that completes a signal; nothing trades on a bar it has not seen.

## The rules

**Intraday (flat by 15:15, or 15:29 where stated):**

* **I1 gap-and-go ORB**: overnight gap |gap| >= 1%; the first 1-minute close beyond the 09:15-09:30 range in the gap's
  direction enters; exit 15:15. (Tradezella's "breakout on gap days"; trend persistence on information days.)
* **I2 first-half-hour to last-half-hour**: each day, rank F&O names by 09:15-09:45 return; long the top decile,
  short the bottom decile at 15:00, exit 15:29. (Gao, Han, Li & Zhou 2018 at the stock level.)
* **I3 15-minute volume surge**: a 15-minute bar with volume >= 3x the same slot's mean over the previous 20 sessions
  and |return| >= 1% enters in its direction at its close; hold 60 minutes.
* **I4 60-minute volume surge**: the same on 60-minute bars (>= 3x, |return| >= 1.5%); hold to 15:15.
* **I5 gap fade**: |gap| >= 2%; enter against the gap at 09:20; exit 15:15.

**Swing (overnight, 5 sessions unless stated, hedged):**

* **S1 high-volume up day**: day volume >= 3x its 20-session mean and close in the top quarter of the day's range ->
  long. (The high-volume premium of [[Result of published price anomalies]], at short horizon.)
* **S2 high-volume down day**: the same with close in the bottom quarter -> short.
* **S3 pullback in an uptrend**: close above the 50-session mean, three down closes in a row on below-average
  volume -> long. (Tradezella's "pullback to a moving average".)
* **S4 hourly RSI dip in an uptrend**: close above the 200-session mean and RSI(14) on 60-minute bars below 25 ->
  long at that bar's close until RSI(14, 60-minute) is above 50 or three sessions pass.

One event per name and rule per holding period (overlapping repeats dropped).

## The bar

Per rule: G3 on monthly means of trade outcomes (both 2016-2021 and 2022-2026, |t| >= 2, right sign) and G4 (random
F&O names entered at the same times, same side and holding). Family: Hansen's SPA and PBO (CSCV) over the nine
monthly series, reported beside the verdicts. Chance any of nine passes G3 by luck: about 1%.

## Predictions

* **J1** no intraday rule passes: gross intraday edges here have been +/-a few bp against 10 bp of cost.
* **J2** S1 and S2 point the way the liquidity finding did (high volume up continues; high volume down continues
  down), gross; at most one passes.
* **J3** SPA does not reject.

Every rule goes into the paper zoo / intraday paper runner with its verdict.

Information, not advice.
