---
type: research
tags:
  - research
  - result
  - intraday
date: 2026-10-15
status: failed
---

# Result of the intraday and swing family on minute bars

Tests [[PRE-REGISTERED intraday and swing family on minute bars]] under [[ADR-0004 one evidence standard]]. This is
the last trial of 2026Q4 (13 of 13). Code `evaluation/intraday_family.py`. Data: 610 names of one-minute history,
2016-2026. Runtime 2.4 hours.

## Verdict: FAIL (no rule passes G3)

| rule | trades | net bp 2016-21 / 2022-26 | t 2016-21 / 2022-26 |
|---|---:|---|---|
| I1 gap-and-go ORB | 18,698 | +0.1 / -0.3 | +0.02 / -0.08 |
| I2 first to last half hour | 2,643 | -18.0 / -20.3 | -13.38 / -28.60 |
| I3 15-minute volume surge | 34,480 | -12.4 / -9.7 | -9.64 / -6.31 |
| I4 60-minute volume surge | 13,396 | -4.5 / +0.1 | -1.35 / +0.03 |
| I5 gap fade | 9,724 | +7.7 / -8.7 | +0.81 / -1.13 |
| S1 high-volume up day (long) | 2,136 | -14.4 / +5.5 | -0.78 / +0.41 |
| S2 high-volume down day (short) | 1,557 | +4.0 / +38.0 | +0.15 / +2.31 |
| S3 pullback in an uptrend | 3,413 | +3.2 / +1.0 | +0.22 / +0.11 |
| S4 hourly RSI dip | 4,346 | -41.1 / -20.1 | -1.66 / -2.09 |

Family: Hansen's SPA p = 0.515 and White's Reality Check p = 0.20 (best: S2), so the best rule is not distinguishable
from luck. PBO = 0.40 with a negative in-sample to out-of-sample slope (-0.42). The G4 null for the four swing rules
returned no value (a defect in the null for overnight holds). It does not affect the verdict, because every rule
fails G3 first.

## Predictions

* **J1** (no intraday rule passes): right. Gross intraday edges were within ±18 bp against 10 bp of cost. I2 and I3
  lose reliably, at t -6 to -29.
* **J2** (S1 and S2 point the way of the liquidity finding, at most one passes): partly right. S2's short works in
  2022-26 (t 2.31) but not in 2016-21, and neither rule passes.
* **J3** (SPA does not reject): right.

## What it means

The minute-bar search adds a 9-rule family to the already-spent price-only search. The cost of trading intraday
(10 bp a round trip) is larger than any gross edge found. This closes the price-derived intraday line: new trials
go to new information (filings, insider records, corporate events), not to more bar patterns. S2's recent-half
result is noted for the knowledge base only. Re-testing it would need a new registration, and any test before
2027Q1 is out of budget.

Information, not advice.
