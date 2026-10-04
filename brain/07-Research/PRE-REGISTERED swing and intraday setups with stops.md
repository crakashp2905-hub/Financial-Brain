---
type: research
tags:
  - research
  - pre-registration
  - swing
  - intraday
  - stops
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED swing and intraday setups with stops

Written and committed before either study runs. Asked for: "work towards intra-day and swing trading, with
stop losses". The honest version of that is to test the classic setups, with stops, after the real costs.

## A. Swing (daily data, long only, liquid universe turnover >= Rs 10 cr/day, 2016-2026)

Signal at the close of day t; **entry at the close of t+1** (one-day lag); exit at the close of t+1+h, h = 5
or 10 sessions. Outcome is market-adjusted: minus the median return of all liquid names over the same window.
Costs: the engine's sized round trip at a Rs 5 lakh order, charged once per trade.

    SW1 pullback in uptrend    above 200d MA, 12-1m momentum > 0, 5-day return <= -5%
    SW2 oversold in uptrend    above 200d MA, RSI(14) < 30
    SW3 breakout               within 1% of the 52-week high, ADX(14) > 25, above the 50d MA
    SW4 strength continuation  above 50d and 200d MA, Bollinger %B(20) > 0.8, ADX(14) > 25

Eight trials (4 setups x 2 horizons). Effect per setup-horizon: mean market-adjusted return per trade, with
one observation per signal date (mean over that date's trades) and t across dates; bar about 3.75.

Stop variant, fixed here: exit early if a daily close is below entry x (1 - 2 x ATR% x sqrt(h)), where ATR% is
`atr_14_pct` at the signal date; the money then earns 6% a year to the end of the window.

Predictions:
* **W1** SW1 and SW2 (reversal setups) have **positive gross** market-adjusted returns at h=5 and a
  **negative or zero net** result: a 0.25-0.5% round trip is more than a 5-day bounce.
* **W2** SW3 (breakout) has gross return near zero; SW4 slightly positive; neither clears the bar net.
* **W3** no setup-horizon clears the bar **net of costs**.
* **W4** the ATR stop does not improve the net mean of any setup (the earlier exit study found stops free but
  useless).
* **W5** the share of trades stopped out is between 10% and 30% for every setup.

## B. Intraday opening-range breakout with a stop (300 names, 498 sessions, minute bars)

Opening range = the high and low of the first 15 minutes (09:15-09:29). From 09:30 to 11:00 the first minute
that **closes** beyond the range triggers an entry at the **next minute's open**, long above the high, short
below the low. Stop at the opposite end of the range; target at 2R (R = entry minus stop); otherwise exit at
15:15. One trade per name per day. Costs: the intraday schedule (STT on the sell leg, brokerage, exchange, GST)
plus impact by liquidity bucket, 14-24 bp round trip. Four trials (long/short x stop at the opposite end /
at the range midpoint); one observation per date, t across dates; bar about 3.75.

Predictions:
* **O1** gross mean return is within +-5 bp per trade in each cell; net is **negative** in all four.
* **O2** win rate is under 45% (the target is rarely reached before the stop).
* **O3** the **short** side does better than the long side, consistent with the earlier finding that
  opening-range breakouts fade.
* **O4** nothing clears the bar.

Pass criteria as written. No setup, threshold, horizon, stop width or cost is changed after seeing a result.
