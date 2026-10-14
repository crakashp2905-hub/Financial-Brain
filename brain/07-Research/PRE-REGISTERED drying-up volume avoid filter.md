---
type: research
tags:
  - research
  - pre-registration
  - liquidity
date: 2026-10-14
status: pre-registered (held-out replication in 2027Q1; out of sample now)
---

# PRE-REGISTERED drying-up volume avoid filter

Written on 2026-10-14 after [[Result of published price anomalies]], which found the registered bottom sides of two
liquidity anomalies significantly bad in both halves while their opposite extremes were weak: an asymmetric, **avoid**
shape. Chosen after seeing it, so no in-sample number counts.

## The filter, fixed

* **DV1 falling volume**: the bottom fifth by rupee volume over the last 60 sessions / over the last 250.
* **DV2 worsening liquidity**: the top fifth by Amihud illiquidity over the last 20 sessions / over the 250 before.
* Outcome per month end: the flagged fifth's equal-weighted return to the next month end minus the equal-weighted
  universe's, **gross** (the use is to avoid names, as N2 was judged), and also net of delivery costs as a long book
  (reported).

## Test 1 - out of sample from 2026-10-15 (no trial)

On the 500 most liquid names, through the zoo's existing books for A11 and A13 (`edge_a11_volume_trend`,
`edge_a13_illiq_shock` hold exactly these fifths): G5 by `fb oos` with direction -1.

## Test 2 - held-out names, 2027Q1 (shares the trial with the high-volume premium test: one registration date, one
universe, the two readings of the same two signals)

Ranks **501-1000** by turnover. G3 with direction -1: mean negative, t <= -2, in 2016-2021 and 2022-2026; G4 against
random same-size sets. If it passes, DV1/DV2 become exclusions in the momentum and carry books' next registered
versions and an avoid flag in the brief - never retrofitted into the current ones.

## Prediction

* Negative in both halves on ranks 501-1000 (65%); passes G3 (35%) - small caps are where liquidity drying up should
  bite hardest.

Information, not advice.
