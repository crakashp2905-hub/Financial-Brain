---
type: research
tags:
  - research
  - pre-registration
  - replication
  - technical
date: 2026-10-03
status: registered, not yet run
---

# PRE-REGISTERED: a ninety-indicator sweep, and why it is allowed

Committed before running. This note exists because the sweep looks like exactly the thing this project
has spent a session learning not to do, and the reason it is permissible is arithmetic rather than
judgement.

## Why a ninety-way search is normally forbidden here

The ledger holds 151 trials. Adding 90 single tests pushes the Bonferroni bar to **t = 3.71**, and each
indicator would have to clear that alone. Searching 200 or 500 pushes it to 3.80 and 3.95. That is the
regime in which [[Naive momentum beats it, and the horizon was a fit]] happened: ADX reached |t| = 5.60
in one tier, cleared every single-test bar, and had the opposite sign in the tiers either side.

## Why it is allowed through the replication harness

The conjunction - the same sign in four independently measured size tiers, each at |t| >= 2.0 - has
probability `2 * Phi(-2)^4 = 5.36e-07` under the null. So:

    candidates   expected false passes
            22             1.18e-05
            90             4.82e-05
           200             1.07e-04
           500             2.68e-04
         2,000             1.07e-03

**Ninety indicators cost 4.8e-05 expected false passes.** The conjunction does the multiple-testing
work that a single test cannot, and it does it without raising any threshold. This is the first time in
this project a wide search has been defensible, and it is defensible only through this specific test.

That is also the limit of the claim: the conjunction permits a wide search for **cross-sectionally
replicating** effects. It permits nothing about horizons, parameters, or portfolio construction, where
every previous search died.

## What is being run

`finta` exposes 90 indicators computed from OHLCV. Adjusted NSE EQ bars come from
`forecasting/bars.py`, keyed on lineage. Indicators returning multiple columns contribute each column
as a separate candidate; the count in the result will state the true number tested, and the multiplicity
correction uses that number, not 90.

Cross-sectional IC at **h20**, the four tiers of ranks 1-500, same method as
[[Four edges that replicate, and the chart patterns that do work]].

## Predictions

**Most will fail, and most will fail by flipping sign between tiers.** That was the pattern for RSI,
stochastic, Williams %R, Bollinger %, ADX, CCI, MFI, OBV slope and MACD histogram, and finta's set is
largely the same family of constructions - moving averages, oscillators and bands over the same price
series.

**I predict fewer than ten pass**, and I predict that those that do will be **re-expressions of the two
ideas already found**: trend or momentum of some lookback, or a volatility or range measure. Concretely:
anything named after a moving average, a channel, a 52-week extreme, or an ATR-like range should
dominate the passers.

**I predict no genuinely new third family.** If something passes that is not a volatility or momentum
re-expression - a volume-flow measure, a cycle or Fisher transform, an Ichimoku component - that is the
interesting outcome and the one worth following.

## What would make this wrong in a way that matters

**Collinearity is not multiplicity.** Ninety indicators over one price series are nowhere near 90
independent tests - many are near-identical. That makes the expected-false-pass figure above
**conservative** in the sense of over-counting the search, but it also means a cluster of passers is one
finding wearing ninety hats, not ninety findings. The result must report how many *distinct* ideas
passed, not how many columns.

**finta computes on raw OHLCV.** The bars are adjusted for corporate actions before the indicators see
them. An indicator computed on unadjusted prices would read a 1:10 split as a 90% crash and any
"signal" from that is an artifact.

**This is in-sample on ranks 1-500.** Anything that passes earns the same held-out test the previous
eight earned - ranks 501-1500 - and that test gets its own registration.

Registered 2026-10-03, before running. Result: [[Result of the ninety-indicator sweep]].

Related: [[Four edges that replicate, and the chart patterns that do work]] ·
[[Result of the pre-registered out-of-sample test]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]]
