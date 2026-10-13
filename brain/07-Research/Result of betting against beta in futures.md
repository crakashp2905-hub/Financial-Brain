---
type: research
tags:
  - research
  - result
  - volatility
  - fno
date: 2026-10-13
---

# Result of betting against beta in futures

Tests [[PRE-REGISTERED betting against beta in futures]] under [[ADR-0004 one evidence standard]]. Trial 6 of 2026Q4
(6 of 8 used). Code `evaluation/bab.py`; series `data/edges/bab_monthly.parquet`.

## Verdict: FAIL (G3, G4)

| | 2016-2021 | 2022-2026 |
|---|---:|---:|
| months | 70 | 57 |
| net per month | -0.26% | -0.08% |
| t | -0.47 | -0.15 |
| Sharpe, annualised | -0.20 | -0.07 |

About 33 names a leg; average shrunk beta 0.79 long, 1.29 short; costs 2 bp a month. G4: min t -0.47 against a
random-leg 95th percentile of +0.25 (random legs average -0.96, so the beta sort is better than random, but not
good enough to be positive).

## Predictions

* **BABa wrong**: not positive in either half.
* **BABb right**: it failed.

## What it means

The volatility family is now closed in every form tested here: long-only low volatility (negative alpha at beta
0.91-0.97), risk-adjusted (Jensen's alpha negative), and beta-neutral levered (this). Indian F&O stocks have not paid
the low-beta premium after the leverage the construction needs. Low volatility stays what the ordering says it is - a
risk control (M1 uses it for weights) - and not a source of return.

Information, not advice.
