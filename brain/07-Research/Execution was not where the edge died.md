---
type: research
tags:
  - research
  - costs
  - execution
  - method
date: 2026-09-27
---

# Execution was not where the edge died

Every backtest here assumed a fill. The cross-sectional tests fill an entire quintile at the
adjusted close; the intraday systems fill at `max(trigger, bar_open)`, conservative about gaps and
still assuming the whole size arrives at one price. The standard warning about that is correct and
worth taking seriously: an assumed fill is where a good backtest goes to die, and the usual fix is
an assumed spread plus an assumed impact coefficient, which replaces one guess with two.

This project has something better. **18.45M minute bars for 100 names** means the fill can be
*measured*: to buy a given rupee amount, walk forward consuming a bounded share of each minute's
real traded volume, and report the volume-weighted price that actually accumulated.

`execution/simulator.py`. No spread model, no impact coefficient, no calibration. The only
parameter is the **participation rate** - what share of a minute's volume an order may take - and
that is a policy a trader chooses, not a market constant to be fitted.

## dual_thrust, assumed fills against measured ones

8,530 trades across 100 names, Rs 5 lakh per trade, both legs walked through real volume:

| | n | net/trade | t |
|---|---:|---:|---:|
| assumed fill (h15) | 8,530 | −0.0792% | −5.17 |
| **measured fill, 10% participation** | 8,264 | **−0.0874%** | **−5.01** |
| measured fill, 3% participation | 8,264 | −0.0876% | −5.04 |

**The assumption was optimistic by 0.82 bps per trade.** The strategy was losing 7.9 bps; it loses
8.7 bps. Nothing about the conclusion changes, and that is the finding.

Supporting numbers, because the headline understates how little execution mattered here:

    mean shortfall            +1.24 bps
    median shortfall          -0.93 bps   (most fills beat the trigger price)
    mean minutes in market     3
    entries not fully filled   5 of 8,264
    orders refused as too late  266

The distribution is skewed exactly as it should be: you are filled easily in calm minutes and badly
in fast ones, so the median fill is slightly *better* than the trigger and a minority are much
worse. And 3% participation gives the same answer as 10%, which says order size is not binding at
Rs 5 lakh in these names.

## So the toll killed it, not the fill

This is the useful conclusion and it is the opposite of what the concern predicted. Against a gross
edge of 9.6 bps and a corrected MIS round trip of 17.5 bps, execution contributes **0.8 bps**.
Restating the whole arithmetic for one trade:

    gross edge            +9.6 bps
    statutory + broker    -17.5 bps      <- this is what kills it
    execution shortfall    -0.8 bps
    ------------------------------
    net                    -8.7 bps

[[The toll was measured with one number and it needed thousands]] found the cost model was charging
the wrong segment and the wrong bucket, a fourfold error. This says the *remaining* uncertainty -
the fill - is an order of magnitude smaller than the error already corrected. The cost model was
the thing worth getting right, and it now is.

## What this does not license

**It is a statement about Rs 5 lakh.** Swept across a 400x range of order size, on 30 names and
349 trades each:

| order | mean shortfall | p90 shortfall | minutes in market | not fully filled |
|---:|---:|---:|---:|---:|
| Rs 5 lakh | −5.1 bps | +71.8 bps | 6 | 2.3% |
| Rs 20 lakh | −6.1 bps | +72.3 bps | 13 | 5.7% |
| Rs 1 crore | −6.3 bps | +74.6 bps | 38 | 15.5% |
| Rs 5 crore | −3.3 bps | +92.4 bps | 104 | **54.2%** |
| Rs 20 crore | +0.9 bps | +118.7 bps | 197 | **89.1%** |

**The mean shortfall does not rise with size.** It wanders between −6 and +1 bps across a
four-hundredfold range, and at n = 349 those means are too noisy to read a trend into anyway - the
8,264-trade estimate at Rs 5 lakh is +1.24 bps, not −5.1. Price impact is not what bounds this.

**Completion is.** `not fully filled` goes from 2.3% to **89.1%**: at Rs 20 crore, nine orders in ten
cannot be finished inside the session at 10% participation. That is a hard capacity limit measured
directly rather than inferred from a coefficient, and it is a more useful number than an impact
estimate because it is a fact about the volume that traded.

**And at size the strategy stops being itself.** Minutes in market go from 6 to 197. A rule that
fires on an opening-range breakout and needs 197 minutes to fill is not trading the breakout; it is
holding a half-session position that happened to be opened by one. The signal and the trade have
come apart, and no cost number expresses that - only the clock does.

The p90 is worth its own line: **+72 bps even at Rs 5 lakh**, rising to +119. The right tail of
shortfall is fat at every size. It is offset in the mean by a body of fills that beat the trigger,
which is why the average is small, but a strategy sized on the average is carrying a tail it has not
priced.

**The spread is still missing.** These are traded prices, so an order walking through the minutes
gets whatever the trades printed at; a *marketable* order pays the far side, and a minute bar does
not say where the bid and offer were. Corwin-Schultz (2012) on this data produced spreads tenfold
too large and was declared unusable, so this is absent rather than approximated, and every number
above is optimistic by that unmeasured amount.

**Nothing here is measured on the cross-sectional strategies**, because minute bars exist for 100
names and those books hold a thousand. The daily results still rest on an assumed close fill.

## And the counterintuitive one worth keeping

At Rs 20 crore in Reliance on 2026-09-18, the measured shortfall was **−11.4 bps: a gain.** The
order took 113 minutes and the price drifted down through the afternoon. The simulator reports what
happened rather than an assumed penalty, so at large size the cost of trading shows up as
**exposure to drift over the fill window** rather than as impact - which is the more honest
description, and it is measured in minutes rather than assumed in basis points.

Related: [[The toll was measured with one number and it needed thousands]] ·
[[Intraday breakouts pay seven times their edge]] · [[The cost number that decided everything]] ·
[[MONEY GATE]]
