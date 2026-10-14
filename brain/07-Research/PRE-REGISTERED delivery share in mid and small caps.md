---
type: research
tags:
  - research
  - pre-registration
  - delivery
date: 2026-10-14
status: pre-registered (runs when 2027Q1 opens)
---

# PRE-REGISTERED delivery share in mid and small caps

Written on 2026-10-14 under [[ADR-0004 one evidence standard]], before any delivery number outside the F&O names
has been computed. **2026Q4's budget is spent; this runs as the first trial of 2027Q1.**

## Why a second test, and why here

[[Result of delivery share]] found the rising-delivery long-short positive but fading in the F&O 200 (t 0.98 / 0.15)
and named the likely reason: in large caps, delivery volume is dominated by index, arbitrage and block flows that
carry no view. In mid and small caps those flows are a much smaller share of delivery, so if delivery share measures
accumulation anywhere it is there. A different universe is a different hypothesis; nothing about the score changes.

## The test, fixed

* **Names**: each rebalance, ranks 201-750 by 20-session turnover (`edges.load` ranks), series EQ, not F&O (no
  futures: the book is long-only in delivery).
* **Score**: as before - mean delivery % over the last 20 sessions minus the mean over the 120 before (at least 15
  and 90 sessions).
* **Rebalance** every 20 sessions from 2016; **long the top fifth**, equal weight, held 20 sessions.
* **Costs**: each name's delivery round trip by liquidity bucket (the edge harness's 30 / 45 / 60 bp), half on each
  unit of weight changed.
* **Measured**: net excess over the equal-weighted ranks 201-750 per period.

G3 both halves (2016-21, 2022-26) and G4 (random books of the same size and turnover from the same names).

## Prediction

* Positive in both halves (60%); passes G3 (25%) - turnover costs in mid caps are 2-3x the futures'.

Information, not advice.
