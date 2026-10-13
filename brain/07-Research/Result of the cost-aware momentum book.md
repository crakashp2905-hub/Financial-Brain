---
type: research
tags:
  - research
  - result
  - momentum
date: 2026-10-13
---

# Result of the cost-aware momentum book

Tests [[PRE-REGISTERED cost-aware momentum book]] under [[ADR-0004 one evidence standard]]. Trial 1 of 2026Q4.
Code `evaluation/momentum_book.py`; series `data/edges/m1_net.parquet`.

## Verdict: FAIL (G3), with a real signal underneath

| | 2016-2021 | 2022-2026 |
|---|---:|---:|
| periods (40 sessions) | 37 | 28 |
| net excess vs equal-weighted 500, per period | **+1.36%** | **+0.75%** |
| t | +1.45 | +1.24 |
| Sharpe of the net excess, annualised | 0.60 | 0.59 |

* **G3 fails**: positive in both halves, t >= 2 in neither.
* **G4 passes, by a distance**: the replication statistic (min t = +1.24) against 200 random books with the same
  size, turnover, weighting and eligible names - their 95th percentile was **+0.14** and their mean **-0.97**. Random
  books at this turnover lose to costs; this one does not. The ordering is real; on 65 periods it is not strong
  enough to be called an edge.
* Turnover **45% of the book per rebalance** (18 of 40 names).

## Predictions

* **M1a right**: positive in both halves.
* **M1b right**, and worse than predicted: G3 failed in *both* halves, not only 2022-26.
* **M1c wrong**: turnover 45%, not below 30%. The top-40/top-100 band does not hold names as long as the
  signal's slow construction suggested - over 40 sessions the composite reorders more than the 12-1 return alone.
* **M1d right**: the N2 exclusion moved the mean by -0.01 percentage points overall (+0.11 in 2022-26, -0.11 in
  2016-21). It touches too few names to matter inside a 40-name book.

## What it means

The same story as every momentum test here, now measured against a null that controls for turnover: **the effect
exists and survives costs, and its t-statistic per half is about 1.2-1.5.** At a Sharpe of 0.6 on the excess, the
t over n periods is roughly 0.6·√(years); reaching 2 in a six-year half would need a Sharpe near 0.8. More history
or a stronger signal - not a different horizon or band - is what moves it; varying either now would be a search.

Per the registration it runs in the paper zoo as `edge_m1_momentum_book` from 2026-10-14 and G5 is judged by
`fb oos` against the 2022-26 band. Nothing about it changes because of the paper record.

Information, not advice.
