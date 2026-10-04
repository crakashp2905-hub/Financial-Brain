---
type: research
tags:
  - research
  - intraday
  - stops
date: 2026-10-04
status: result
---

# Result of the opening-range breakout with stops

Predictions committed at `d018e64`. 300 names x 498 sessions of Kite minute bars, 251,000 simulated trades.
Opening range = first 15 minutes; entry at the next minute's open after the first close beyond it (09:30-11:00);
stop at the opposite end of the range ("full") or its midpoint ("mid"); target 2R; flat by 15:15. Fills are hard
on the strategy: a gap through the stop fills at the open, a minute touching both stop and target counts as a
stop. Costs: the intraday schedule plus impact, 14 bp (top 100 by turnover) or 24 bp round trip.

    cell            trades   gross/trade     t    net/trade      t     mean R  target  stop  time  net win%
    long  / full    58,391     -0.2 bp     -3.35   -20.9 bp    -11.6   -0.01      9%    30%   61%    39%
    long  / mid     58,389     +0.0 bp     -3.85   -20.7 bp    -17.2   -0.01     23%    54%   23%    35%
    short / full    67,272     +6.1 bp     -0.91   -14.7 bp     -8.4   +0.05      8%    29%   64%    43%
    short / mid     67,269     +5.4 bp     -0.95   -15.3 bp    -12.3   +0.07     23%    51%   26%    39%

    O1 gross within +-5 bp, net negative in all four        MOSTLY  long cells -0.2/0.0 bp; short cells +6.1/+5.4 bp, just outside; net negative in all four
    O2 win rate under 45%                                   PASS    35-43% net
    O3 the short side does better than the long side        PASS    +6 bp vs 0 gross; -15 vs -21 net
    O4 nothing clears the bar                               PASS    the |t| > 3.75 entries are significant LOSSES, none a gain

**An opening-range breakout is a coin flip with a fee.** Gross, the trade earns nothing (mean R of -0.01 to
+0.07); the wide stop is reached in 30% of cases and the 2R target in only 8-9%, and 61-64% of trades just
drift to the 15:15 exit. The tight (midpoint) stop gets hit half the time and the target a quarter, but the
mean R does not change: the stop moves the payoff around, it does not create one. Then 14-24 bp of round-trip
cost on a ~0 gross trade is a reliable loss of 15-21 bp every trade, about 7-9 times the gross spread.

**Stops here are a risk shape, not an edge.** The stop and target turn a distribution of outcomes into a
different distribution with the same mean. That matches the daily exit study. The only thing a stop can do
reliably is cap how much one bad trade can lose, and that has a price in being stopped out of trades that
would have recovered.

**Shorts slightly beat longs**, consistent with the opening-range breakouts-fade finding from the intraday
feature study (IC -0.010 for `or_breakout`), but +6 bp gross is not significant and does not survive costs.

Related: [[PRE-REGISTERED swing and intraday setups with stops]] ·
[[Result of the intraday study and the slippage calibration]] · [[Result of exit rules]]
