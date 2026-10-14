---
type: research
tags:
  - research
  - pre-registration
  - liquidity
date: 2026-10-14
status: pre-registered (out of sample now; held-out replication in 2027Q1)
---

# PRE-REGISTERED high-volume return premium

Written on 2026-10-14, after [[Result of published price anomalies]], where two registered liquidity anomalies came
out **significantly reversed** in both halves: names whose rupee volume was rising (A11 reversed) and whose price
impact per rupee was falling (A13 reversed) beat the rest. The reading - the high-volume return premium of Gervais,
Kaniel & Mingelgrin (2001), stocks with unusually high volume subsequently rise through visibility - was chosen
**after** seeing those results. So no in-sample number here is evidence, and neither book gets an in-sample verdict.

## The books, fixed (signals exactly as in the anomaly test, sides reversed)

* **HV1 rising volume**: long the top fifth by rupee volume over the last 60 sessions / over the last 250.
* **HV2 improving liquidity**: long the bottom fifth by Amihud illiquidity over the last 20 sessions / over the 250
  before.
* Both: equal weight, rebalanced at month ends, held to the next, net of each name's delivery round trip by liquidity
  bucket, measured as net excess over the equal-weighted universe.

## Test 1 - out of sample, from 2026-10-15 (no trial)

Universe: the 500 most liquid names, as found. Paper-traded in the zoo (`edge_hv1_rising_volume`,
`edge_hv2_improving_liquidity`); judged by `fb oos` (G5): 12 monthly rebalances with a positive net mean, early fail
after three month ends below the band.

## Test 2 - held-out names, the first trial of 2027Q1

Universe: names ranked **501-1000** by 20-session turnover each month end - never part of the anomaly test (it used
ranks 1-500), as N2 was replicated on the same ranks. Same books, same costs (these are mostly the 60 bp bucket).
G3 (|t| >= 2, positive, in 2016-2021 and 2022-2026) and G4 (same-size, same-turnover random books). It runs on
2027-01-01 or later; nothing about it changes before then.

## Prediction

* HV1 and HV2 positive in both halves on ranks 501-1000 gross (60%); pass G3 net of 60 bp costs (20%).

Information, not advice.
