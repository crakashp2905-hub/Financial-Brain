---
type: research
tags:
  - research
  - result
  - fundamentals
  - earnings
date: 2026-10-15
status: failed
---

# Result of earnings surprise drift out of sample

Registered in [[PRE-REGISTERED earnings surprise drift out of sample]] (2026-10-13). Run on 2026-10-15 by
`evaluation/fundamental_tests.py` (`sue_table`, `x3_cohorts`), once the March-2025 quarter onward became available
from NSE Integrated Filing. Recorded in the ledger as trial **X3** (amendment 4).

## What ran

* 20,160 first-reported quarterly statements (consolidated where filed, else standalone) for 1,270 names; 10,115
  had a SUE (eight quarters of history, at least six); **3,189 were filed after 2024-12-31**.
* 14 monthly cohorts (2025-01 to 2026-08, months with at least 10 filers among the 500 most liquid; quarter-end
  months without filings drop out). Long the top fifth by SUE from the close two sessions after filing, held 40
  sessions, net of delivery round trips, excess over the equal-weighted 500.

## Result: FAIL

| | value | bar |
|---|---|---|
| cohorts | 14 | >= 12 |
| net mean excess per cohort | **-2.08%** | > 0 |
| t | **-2.25** | >= 2 |
| random same-month filers, 95th percentile of t | -2.97 | real above it |

Twelve of 14 cohorts were negative. The top fifth by surprise **did** beat random cohorts drawn from the same months'
filers (its t, -2.25, is above that null's 95th percentile, -2.97). The whole filer pool lost to the equal-weighted
500 over 2025-26, net of costs, and the high-surprise names lost less. That is a relative ranking inside a losing
pool, not a tradable long. It also does not pass the registered bar, which requires a positive mean.

## Predictions

* **X3a** (positive net mean): **wrong**.
* **X3b** (t below 2 at the first cohorts): right, but in the wrong direction.

## What it means

The in-sample small-tier SUE effect (t 3.4 on 2019-24) did not carry into 2025-26 as a long book. There is no
re-test with a different window, holding or benchmark: that would be a new registration and a new trial. The
relative ordering, high surprise beating low surprise among filers, is noted for the knowledge base only.

Information, not advice.
