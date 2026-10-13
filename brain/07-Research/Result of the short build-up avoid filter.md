---
type: research
tags:
  - research
  - result
  - fno
date: 2026-10-13
---

# Result of the short build-up avoid filter

Tests [[PRE-REGISTERED short build-up avoid filter]] under [[ADR-0004 one evidence standard]]. Trial 2 of 2026Q4.
Code `evaluation/short_buildup.py`; series `data/edges/f3_excess.parquet`.

## Verdict: FAIL (G3, G4)

| | 2019-2021 | 2022-2026 |
|---|---:|---:|
| dates (every 20 sessions) | 36 | 58 |
| flagged names' 20-session return minus the F&O mean | **+0.58%** | -0.28% |
| t (in the registered direction, negative = works) | -1.24 | +0.93 |
| as a futures short, net of 10 bp | -0.68% | +0.18% |

About 15 names flagged per date. The 2019-21 sign is the **opposite** of the mechanism: names with falling prices
and rising open interest went on to *outperform* - consistent with short-term reversal in a period of violent
reversals (2020), and with futures OI rising for hedging as much as for informed shorting. The 2022-26 sign is right
and small. G4: min t -1.24 against a random-set 95th percentile of +0.41.

## Predictions

* **F3a wrong**: not negative in both halves.
* **F3b right**: it failed.

## What it means

With F1 and F2, all three single-stock OI configurations have now failed on this data. Single-name futures
positioning, at a 5-session change, does not carry a usable signal here. Paper-traded in the zoo as
`edge_f3_short_buildup` with this verdict beside it, as every candidate is.

Information, not advice.
