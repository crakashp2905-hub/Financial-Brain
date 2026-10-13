---
type: research
tags:
  - research
  - result
  - delivery
  - fno
date: 2026-10-14
---

# Result of delivery share

Tests [[PRE-REGISTERED delivery share]] under [[ADR-0004 one evidence standard]]. Trial 8 of 2026Q4 (8 of 8 - the
quarter's budget is spent). New data: NSE's security-wise delivery position (MTO), 2,920 sessions 2015-01-01 to
2026-10-13, 5.94M rows (`fb delivery`, `ingest/delivery.py`), now in the evening refresh. Code
`evaluation/delivery_share.py`; series `data/edges/d1_net.parquet`.

## Verdict: FAIL (G3, G4 narrowly)

| | 2016-2021 | 2022-2026 |
|---|---:|---:|
| periods (20 sessions) | 75 | 58 |
| net per period, long top fifth / short bottom fifth | +0.46% | +0.06% |
| t | +0.98 | +0.15 |
| gross per period | +0.58% | +0.18% |

Costs 0.12% a period (5 bp one way on ~2.4 units of weight changed). G4: min t +0.15 against a random-leg 95th
percentile of +0.34 (random legs average -0.94, so the delivery sort does carry some ordering).

**D1 right** on both counts: positive in both halves, short of the bar. The effect, if any, has faded since 2021 -
consistent with delivery share in large caps being dominated by index, arbitrage and block flows that carry no view.

## What the data is still good for

Delivery share joins the brief's context for a name and the deep library's inputs; a test in **mid and small caps**
(where block and arbitrage flows are a smaller share) is a different universe and would be a new registration in
2027Q1. On paper in the zoo as `edge_d1_delivery_ls`.

Information, not advice.
