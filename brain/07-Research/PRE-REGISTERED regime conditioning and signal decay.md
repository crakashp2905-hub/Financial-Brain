---
type: research
tags:
  - research
  - pre-registration
  - regime
  - signal-decay
date: 2026-10-03
status: registered, not yet run
---

# PRE-REGISTERED: regime conditioning and signal decay

Committed before running. Result goes in a separate note; git history is the proof of ordering. Same
discipline as [[PRE-REGISTERED out-of-sample test of the eight survivors]], which is the only procedure
here that has produced a positive result rather than destroyed one.

Two questions, both untested on the eight survivors, both about **failure modes** rather than
performance - which is the right thing to ask next about signals that have already passed a held-out
test.

## Question 1: when does it fail?

An edge that is positive on average can be untradeable if it loses badly in the regime you are
currently in. `market_regime` v2 classifies every session into five states, with usable counts:

    RISK_ON    1,253 sessions
    RISK_OFF     499
    NEUTRAL      467
    NARROW       239
    CRISIS        78

CRISIS at 78 sessions is thin and will be reported with that caveat rather than dropped - 78 sessions
is the sample the market actually provided.

### Predictions, from the literature and opposed on purpose

**Momentum crashes in drawdowns.** Daniel & Moskowitz (2016) document momentum crashes concentrated in
bear markets and high-volatility states. So:

    mom_12_1        IC falls in RISK_OFF, and falls further in CRISIS; may go negative in CRISIS
    dist_52w_high   same direction

**Low volatility strengthens in drawdowns.** The defensive-anomaly story says low-vol names win most
when the market falls. So:

    vol_60          IC becomes MORE negative in RISK_OFF and CRISIS
    atr_pctile_250  same direction

These predictions point **opposite ways in the same regimes**, which is what makes the pair
informative. If both hold, the two families are genuine complements and a book holding both is
diversified by construction. If both fail, either the regime labels are not capturing what they claim
or the signals are not the anomalies they resemble.

I expect the momentum prediction to hold and am less sure about low volatility.

## Question 2: how long does the edge last, and can it ever pay for itself?

This is the project's central unanswered question, and it has to be approached carefully because the
obvious approach is the one that already failed. Sweeping rebalance horizons on realised P&L and taking
the best is exactly what produced h60 - worth +246% in sample and **-7.49% out of it**
([[Naive momentum beats it, and the horizon was a fit]]).

So: **the decay curve is a description, not a menu.** I am measuring the shape of IC against horizon. I
am **not** selecting a horizon from it and reporting that horizon's P&L as a finding. If this note's
result is later used to pick a horizon, that choice needs its own registration and its own holdout.

Measured on the **held-out tiers only** (ranks 501-1500), so it stays out of sample:

    horizons: h1, h3, h5, h10, h20, h40, h60, h120

### The cost crossing

IC converts to a gross spread by Grinold (1994): `alpha ~ IC * sigma_cs * z`, where `sigma_cs` is the
cross-sectional dispersion of forward returns at that horizon and `z` is the mean standardised score of
the selected slice (about 1.76 for a top decile). Measured dispersion of h20 returns is 12.3% at deep
and 15.1% at micro.

A round trip costs roughly 80 bp. So for each horizon:

    gross per rebalance  =  IC(h) * sigma_cs(h) * 1.76
    cost per rebalance   =  ~0.80%
    net per rebalance    =  gross - cost

**Prediction:** net is negative at h1-h5 (costs dominate), crosses zero somewhere between h10 and h40,
and the gross term stops growing well before h120 - so there is an interior optimum. Momentum should
persist longest; the candle patterns should be dead by h20, which h5-versus-h20 already hinted at.

If net never turns positive at any horizon, that is the answer to whether this is tradeable at all, and
it is a result rather than a disappointment.

## What would make me wrong in a way that matters

**Grinold assumes a linear IC-to-return map and a normal cross-section.** Indian equity returns are
fat-tailed, so the z of 1.76 overstates the top decile's mean score. That biases the gross term
**upward**, which means the crossing horizon is later than this will say and any positive net is an
overestimate. Stated now so the result cannot be read as a floor.

**Regime is measured contemporaneously.** A regime label for session t uses data up to t, but the
forward return runs past t - so a signal measured "in CRISIS" is really "entering a window that began in
CRISIS". That is the tradeable framing, and it is not the same as "the whole window was CRISIS".

Registered 2026-10-03, before running. Result: [[Result of the regime and decay tests]].

Related: [[Result of the pre-registered out-of-sample test]] ·
[[Four edges that replicate, and the chart patterns that do work]] ·
[[Naive momentum beats it, and the horizon was a fit]] · [[The cost number that decided everything]]
