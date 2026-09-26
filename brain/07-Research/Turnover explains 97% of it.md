---
type: research
tags:
  - research
  - costs
  - strategy
date: 2026-09-26
---

# Turnover explains 97% of it

Eight indicators from the PineScript canon, pre-registered as [[h17]] and tested as one
batch. All eight rejected. The interesting part is not that they failed but *how
predictably*.

| Strategy | t | turnover |
|---|---:|---:|
| supertrend | -1.59 | **0.45%** |
| donchian_55_20 | -2.54 | 2.23% |
| keltner_breakout | -4.01 | 1.89% |
| awesome_oscillator | -5.30 | 5.18% |
| ichimoku_cloud | -7.56 | 5.65% |
| cmf_positive | -8.86 | 4.82% |
| **pivot_breakout** | **-24.22** | **17.36%** |

**Correlation between turnover and |t|: +0.985.**

Turnover explains 97% of the variance in the outcome. These are not eight different
findings about eight different ideas - Ichimoku and Chaikin money flow and floor-trader
pivots have nothing in common as theories of the market. They are eight measurements of
the same toll, ordered by how often each rule trades.

Floor-trader pivots recompute from every session, so the book turns over six times a
month and the statistic reaches -24. Nothing in that number is a market finding.

## The one held open

`vwap_reversion` was the only entry [[h17]] gave a real chance, because intraday volume is
the one genuinely new input Kite brought and VWAP is what institutional execution is
benchmarked against. Session VWAP over 18.45M minute bars:

| Close vs session VWAP | n | forward 5 sessions |
|---|---:|---:|
| above VWAP by >0.5% | 8,671 | +0.5257% |
| below VWAP by >0.5% | 11,212 | **+0.5608%** |
| within 0.5% | 29,134 | +0.1713% |

The reversion **is there**. Names closing below their VWAP do outperform those closing
above, consistently, over 19,883 observations. The edge is **+0.0351%** and a round trip
costs **0.709%** - twenty times larger.

The pre-registration predicted exactly this: that the mechanism would be real and that it
would still fail the cost gate. Both halves held.

## What the whole day says

The honest summary is no longer "these strategies do not work". Across today's tests the
gross edges were repeatedly **real and repeatedly tiny**:

    opening-range breakout        +0.043%     cost 16x
    dual_thrust                   +0.096%     cost  7x
    VWAP reversion (5 sessions)   +0.035%     cost 20x
    intraday candle patterns      +0.005%     cost 140x

Indian cash equity charges **20 basis points of securities transaction tax alone**, before
any spread or impact. Every technical rule tested here is trying to pay that out of an
edge measured in single basis points.

That is not a fact about technical analysis. It is a fact about **this market at these
horizons**, and it points the research somewhere specific: either to holding periods long
enough that a 71bp round trip is amortised over months rather than days, or to signals
with edges an order of magnitude larger - which, per
[[A hundred strategies and the data that blocks half of them]], means fundamentals.

## Excluded honestly

**Parabolic SAR** is recursive in its own previous value, the extreme point and an
acceleration factor - a state machine no window function expresses. Excluded on
computability, stated in advance rather than approximated.

**Supertrend** was implemented without its trailing band lock, for the same reason. That
makes it a slightly different indicator and its source line says so. Implying a faithful
implementation would have been cheaper and wrong.

Related: [[h17]] · [[Intraday breakouts pay seven times their edge]] ·
[[The cost number that decided everything]] · [[Running the strategies on noise]]
