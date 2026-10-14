---
type: research
tags:
  - research
  - pre-registration
  - index
  - technical
date: 2026-10-14
status: pre-registered
---

# PRE-REGISTERED index mean reversion and VWAP

Written on 2026-10-14 under [[ADR-0004 one evidence standard]], before anything below is computed, from sources
the owner supplied: QuantPedia's *Automated Trading Edge Analysis* (rules found on SPY) and Tradezella's
*Trading Edge* (an intraday VWAP-bounce setup). One trial for the family of nine.

**Budget.** 2026Q4's eight trials are spent. This is registered now and paper-traded from the next session; **its
in-sample test runs only when the owner raises the 2026Q4 budget or when 2027Q1 opens**, whichever comes first. The
registration does not change in between.

## What is new here, and what is not

The project has tested Nifty *trend* timing (200-day, 50/200 cross), a volatility regime and breadth
([[Result of market timing]]), turn-of-month, pre-holiday, overnight and intraday momentum. It has not tested
**short-horizon index mean reversion** - QuantPedia's strongest SPY results - nor a VWAP setup. QuantPedia's
low-volatility long is excluded: it is the volatility regime already tested. The Medium/Substack article's named
edges need data this project does not have (US Form 4, US contract awards, ETF flows, ship and flight tracking,
weather) or are out of scope (crypto liquidations); option-premium selling was tested on synthetic Nifty options.

## Prices and costs

Nifty 50 spot at **15:15** each session from one-minute bars (`data/edges/nifty50_minute.parquet`), a proxy for the
near-month future, which is what would be traded: the signal is computed on the 15:15 price and the position is
taken at it, so no rule trades on a price it has not seen. Return of a held position: 15:15 to the next session's
15:15. Costs: 2 bp one way per unit of position changed (Nifty futures). Cash at 6.5% a year.

## The rules (daily, position in {-1, 0, +1})

* **Q1 extreme down day, long**: long for one session after a 15:15-to-15:15 return among the 25 lowest of the
  last 250.
* **Q2 extreme up day, short**: short for one session after a return among the 25 highest of the last 250.
* **Q3 RSI long**: RSI(14) below 35 enters long; RSI above 55 exits.
* **Q4 RSI short**: RSI(14) above 65 enters short; RSI below 55 exits.
* **Q5 above MA10**: long while the price is above its 10-session mean, flat otherwise.
* **Q6 dip below MA10**: below the 10-session mean enters long; held until the price is the highest of the last 10.
* **Q7 cross above MA10, three sessions**: a move from below to above the 10-session mean enters long for three
  sessions.
* **Q8 10-session extremes**: a 10-session low enters long, a 10-session high enters short; held until the
  opposite.

**The edge being claimed is timing, not market exposure.** Each rule's daily outcome is
`(w_t - wbar_t) x (r_{t+1} - rf) - costs`, where `wbar_t` is the rule's own average position over the previous 250
sessions - the exposure a buy-and-hold of the same size would have carried - so a rule that is simply long a rising
market earns nothing here.

## V1 VWAP bounce, intraday

From 09:45 to 11:30, if the price is above the session's VWAP (from the minute bars' typical price and volume) and
above the day's open, the first one-minute bar whose low touches VWAP and closes back above it enters **long** at
that bar's close; the mirror (below VWAP and the open, high touches VWAP and closes below) enters **short**. One
trade a day, closed at 15:15. Outcome: the trade's return - 4 bp. (Index minute volume is the futures' traded volume
in this file; where it is zero the bar's weight is one.)

## The bar

A rule passes if G3 (monthly sums of its daily outcomes: mean positive, |t| >= 2, in 2016-2021 **and** 2022-2026)
and G4 (above the 95th percentile of 200 random circular shifts of its own position series against the returns -
same exposure, same turnover, timing destroyed). Nine rules: the chance any passes G3 by luck is about 1%.

## Predictions

* **R1** Q1 and Q3 (the long mean-reversion rules) are positive in both halves; at most one passes.
* **R2** Q2, Q4 and Q8's short side lose: Indian large caps have drifted up and shorting strength has paid badly.
* **R3** V1 fails: the intraday studies here found no gross edge in direction on the index.

Every rule runs in the paper zoo from the next session with this registration beside it.

Information, not advice.
