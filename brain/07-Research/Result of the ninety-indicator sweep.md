---
type: research
tags:
  - research
  - pre-registration
  - replication
  - technical
date: 2026-10-03
status: result
---

# Result of the ninety-indicator sweep

Predictions committed at `d3fcadf` before running. `finta`'s 90 indicators expanded to **113 candidate
columns** after multi-column outputs were split and constant or mostly-missing columns dropped. Tiers
1-500, h20, same conjunction as everything else.

    113 candidates tested
      7 replicated (same sign in all four tiers, |t| >= 2.0 in each)
      7 survived multiplicity correction
     47 flipped sign between tiers

## Two predictions right, one wrong, and the wrong one is the finding

**"Most will fail, and most by flipping sign between tiers."** Right - **47 of 113**, the single most
common outcome, exactly as RSI, stochastic, Williams %R, ADX, CCI, MFI, OBV slope and MACD histogram
did before.

**"Fewer than ten pass."** Right - seven.

**"Those that pass will be re-expressions of the two ideas already found... no genuinely new third
family."** Wrong, and I wrote that if a volume-flow measure passed it would be "the interesting
outcome and the one worth following". Three did.

## But first: one of the seven is a lookahead, and it is the strongest

    ICHIMOKU_CHIKOU   +1   min|t| 13.37   ICs +0.064 +0.075 +0.086 +0.102

An IC of +0.102 rising monotonically across tiers at t = 13.37 is not a discovery, it is a red flag,
and it was. finta computes the Chikou span as

    chikou_span = ohlc["close"].shift(-chikou_period)

A **negative** shift. At row *t* the column holds `close[t+26]`. The sweep was correlating a future
close against a future return, which is why it was the best thing in the table by a factor of three.

That is the charting convention - Chikou is *drawn* 26 bars back - and it is correct for a chart and
catastrophic for a backtest. **An audit of every finta indicator found ICHIMOKU to be the only one that
shifts forward**, so the contamination is contained, but the lesson is not: a third-party indicator
library is written for plotting, and plotting conventions include drawing future data in the past.

`ICHIMOKU_CHIKOU` is disqualified. Six remain.

## Of the six, two are volatility and one is partly momentum

Cross-sectional rank correlation of each survivor against the four already known:

    survivor            mom_12_1   dist_52w_high    vol_60   atr_pctile_250
    VBM                   +0.060        -0.137     +0.791        +0.243
    BBWIDTH               +0.013        -0.157     +0.603        +0.177
    EFI                   +0.080        +0.318     +0.134        -0.038
    CHAIKIN               +0.050        +0.267     -0.024        -0.032
    ADL                   +0.173        +0.113     +0.021        -0.096
    WOBV                  +0.103        +0.091     -0.019        +0.022

**VBM** (volatility-based momentum) at rho = +0.79 and **BBWIDTH** (Bollinger band width) at +0.60 are
the volatility family wearing different names, and both carry the same sign as `vol_60`. They are
confirmation, not discovery - which is the half of the prediction that held.

**EFI** (Elder force index) at rho = +0.32 with the 52-week-high signal is partly momentum.

## Three volume-flow measures are not any of the known families

`CHAIKIN`, `ADL` and `WOBV` correlate at most **0.27** with anything already found, and `ADL` and
`WOBV` at most 0.17. On the distinctness test the registration demanded, these are a third idea.

    column     dir   min|t|      adj p    ICs by tier
    CHAIKIN     +1     2.93   1.88e-09    +0.014 +0.027 +0.007 +0.012
    ADL         +1     2.39   1.09e-06    +0.019 +0.037 +0.013 +0.006
    WOBV        -1     2.24   5.74e-06    -0.008 -0.011 -0.024 -0.010

**And I would not act on them yet, for three reasons.**

The ICs are **small** - 0.006 to 0.037 against momentum's 0.024 to 0.053 and volatility's 0.033 to
0.068. At the dispersion measured in [[Result of the regime and decay tests]] an IC of 0.015 produces a
gross spread well under the 80 bp round trip at any horizon that matters.

They are **not monotone**. Every effect that has held up in this project showed a clean size gradient;
these wander (+0.014, +0.027, +0.007, +0.012). That is what the conjunction is designed to tolerate -
it asks only for one sign - but the absence of structure is a real difference from the four that
passed the held-out test.

And all three are **cumulative sums**. ADL and on-balance volume are running totals from the start of
the series, so a name's cross-sectional *level* encodes how long it has been listed and how much volume
it has ever done. That is a plausible proxy for listing age or size rather than a tradeable signal, and
nothing here separates the two.

## What the sweep cost, and what it bought

The registration's arithmetic held: 113 candidates through the conjunction carry **6.1e-05** expected
false passes, against a single test that would have required every one of them to clear t = 3.72 alone.
The search was affordable, and it was affordable only through this test.

What it bought: confirmation that the volatility family is robust across two more parameterisations, a
third candidate family that needs its own held-out test, and one library-level lookahead bug that would
have produced a spectacular fake result in any project testing Ichimoku without reading the source.

What it did not buy: anything tradeable. Six survivors, of which three are restatements and three are
too small to pay 80 bp.

## Next, and it needs its own registration

`CHAIKIN`, `ADL` and `WOBV` on **ranks 501-1500**, which no measurement has touched, with the direction
fixed as found here and no refitting - the same test the first eight passed. Plus a check that
separates "cumulative volume signal" from "listed longer, traded more", because if it is the second
there is nothing here at all.

Related: [[PRE-REGISTERED ninety indicator sweep]] ·
[[Four edges that replicate, and the chart patterns that do work]] ·
[[Result of the pre-registered out-of-sample test]] · [[Result of the regime and decay tests]]
