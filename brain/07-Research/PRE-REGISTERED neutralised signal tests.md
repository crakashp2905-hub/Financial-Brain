---
type: research
tags:
  - research
  - pre-registration
  - neutralisation
date: 2026-10-06
status: pre-registered
---

# PRE-REGISTERED neutralised signal tests

Written before computing. Question from the review: does a signal *select stocks*, or does it load on a known
exposure - market beta, sector, size, volatility - that would be earned more cheaply by holding that exposure?

## Method

* Sessions: every 20th session, 2017-01 to 2026-08. Universe: the 500 most liquid names by 20-day turnover on
  that session (point in time).
* Outcome: the forward 20-session return (60 reported, with overlapping windows stated).
* Controls, all known on the session:
  * **beta** to Nifty 500 over the previous 250 sessions
  * **sector**: each name's home sector (the sector index its market-removed returns follow most closely), measured
    once a year from the previous 250 sessions only (`graph/exposure`); names with no positive sector link form
    their own group
  * **size**: log 20-day turnover - there is no point-in-time market capitalisation for every name, so turnover
    stands in for it, and is called a proxy
  * **volatility**: 60-session volatility
* Levels: raw; beta; beta + sector; beta + sector + size; beta + sector + size + volatility (not applied to the
  low-volatility signal itself, where it is undefined).
* At each level, both the signal (as normal scores of its ranks) and the outcome are residualised on the controls
  by cross-sectional least squares; the statistic is the correlation of the residuals. One value per session; t
  across sessions. Running Bonferroni bar 3.79.
* Signals, signed so positive means "works": 12-1 momentum, nearness to the 52-week high, low 60-day volatility,
  20-day reversal, the composite.

## Predictions (h = 20)

* **N1** Momentum keeps at least half its raw IC after beta + sector + size.
* **N2** Low volatility loses **more than half** its IC once beta and size are removed: much of it is a low-beta,
  large-name bet.
* **N3** The 52-week-high signal keeps at least half its raw IC after beta + sector + size.
* **N4** The composite keeps at least half, and its fully neutral IC still clears the Bonferroni bar.
* **N5** Reversal keeps at least half (a short-horizon, stock-specific effect).
