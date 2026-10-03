---
type: research
tags:
  - research
  - pre-registration
  - replication
  - volume
date: 2026-10-03
status: registered, not yet run
---

# PRE-REGISTERED: holdout and confound test for the volume family

Committed before running. Result in a separate note.

[[Result of the ninety-indicator sweep]] found three volume-flow measures that replicate across all
four tiers and correlate at most 0.27 with momentum or volatility. They are the first candidate third
family this project has seen. They are also small, non-monotone, and cumulative sums - so there are two
tests, not one, and the second may matter more.

## Test 1: the holdout, direction fixed

Ranks **501-1500**, which no indicator measurement has touched. No refitting, no reselection, direction
taken from the sweep:

    CHAIKIN   predicted POSITIVE
    ADL       predicted POSITIVE
    WOBV      predicted NEGATIVE

**Pass** = predicted sign in both the `deep` (501-1000) and `micro` (1001-1500) tiers with |t| >= 2.0
in each. Same rule the first eight passed.

## Test 2: the confound, which is the one I expect to bite

`ADL` and on-balance volume are **running totals from the first bar of the series**. A name's level
therefore encodes two things that have nothing to do with a signal:

* how long it has been listed - more bars, more accumulation;
* how much volume it has ever traded - a size proxy.

A cross-sectional rank on such a level could be ranking listing age and size while looking like a flow
signal. The sweep cannot distinguish these, so:

**2a.** Compute the cross-sectional IC of **listing age** (bars of history available at that session)
and of **cumulative traded volume** directly, as if each were a signal. If either has an IC comparable
to the three candidates, the candidates are plausibly proxies.

**2b.** Rank-correlate each candidate against listing age and cumulative volume within tier and
session. High correlation plus a comparable IC is the signature of a proxy.

**2c.** Re-measure each candidate's IC after **differencing** it - the 20-session *change* in the
level rather than the level. A cumulative sum's change is stationary and carries no age or size
information. If the signal is flow, the differenced version survives. If the signal is age, it
vanishes.

## Predictions

**On the holdout I expect CHAIKIN to pass and ADL and WOBV to be marginal.** CHAIKIN is an oscillator -
a difference of two EMAs of the accumulation line - so it is already differenced and carries no level.
ADL and WOBV are raw levels and are the ones exposed to the confound.

**On the confound I expect the differenced versions to be weaker than the levels for ADL and WOBV, and
roughly unchanged for CHAIKIN.** If that pattern appears, the honest conclusion is that the "third
family" is one indicator (CHAIKIN) and two artifacts.

**I expect listing age to have a non-trivial IC of its own.** Older listings in an Indian universe
skew toward larger, more established names, and the size gradient running through every result in this
project means an age signal will not be zero.

If all three survive both tests, that is a genuine third family and the first one this project has
found that is not in the textbooks.

## What would still be unresolved either way

Even a clean pass leaves the ICs at 0.006 to 0.037, which at the dispersion measured in
[[Result of the regime and decay tests]] does not clear an 80 bp round trip at any horizon. Passing
makes this a real effect, not a tradeable one, and the distinction has been the whole story of this
project.

Registered 2026-10-03, before running. Result: [[Result of the volume family holdout]].

Related: [[Result of the ninety-indicator sweep]] ·
[[Result of the pre-registered out-of-sample test]] ·
[[Four edges that replicate, and the chart patterns that do work]]
