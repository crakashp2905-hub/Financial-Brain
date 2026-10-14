---
type: research
tags:
  - research
  - result
  - anomalies
date: 2026-10-14
---

# Result of published price anomalies

Tests [[PRE-REGISTERED published price anomalies]] under [[ADR-0004 one evidence standard]], trial 10 of 2026Q4
(amendment 2). Code `evaluation/anomalies.py`, `evaluation/family_validation.py`; results
`data/edges/a10_result.json`, series `data/edges/a10_series.parquet`.

## Verdict: FAIL - 0 of 16

Long the registered best fifth of the 500 most liquid names, net of delivery costs, excess over the equal-weighted
500, monthly (128 months; A2 104):

| anomaly | net / month 2016-21 / 2022-26 | t | rank IC (t) | IC t by tier 1-50 / 51-100 / 101-250 / 251-500 | long-short gross (t) | turnover |
|---|---|---|---|---|---|---|
| A1 intermediate momentum | +0.25% / +0.44% | +1.03 / +1.70 | +0.035 (3.4) | +0.5 / +1.4 / +2.6 / +3.9 | +1.13% (3.3) | 41% |
| A2 long-term reversal | -0.16% / -0.12% | -0.38 / -0.66 | -0.002 (-0.1) | -1.0 / -0.3 / -0.4 / +0.1 | +0.11% (0.3) | 30% |
| A3 return seasonality | +0.04% / -0.19% | +0.16 / -0.95 | +0.031 (3.5) | +0.9 / +1.8 / +1.2 / +4.8 | +0.75% (2.6) | 79% |
| A4 idiosyncratic vol | -0.25% / -0.48% | -0.75 / -1.43 | +0.055 (3.6) | +1.3 / +1.2 / +3.2 / +4.6 | +0.08% (0.2) | 26% |
| A5 MAX | -0.17% / -0.55% | -0.76 / -1.83 | +0.041 (3.7) | +1.7 / +1.2 / +3.1 / +4.4 | +0.02% (0.1) | 62% |
| A6 idiosyncratic skew | -0.56% / -0.41% | -3.18 / -1.74 | +0.016 (2.2) | +0.6 / +1.1 / +1.6 / +2.4 | -0.18% (-0.7) | 45% |
| A7 vol of vol | +0.13% / -0.02% | +0.59 / -0.13 | +0.034 (4.7) | +0.4 / +2.3 / +3.4 / +4.8 | +0.76% (2.8) | 27% |
| A8 downside beta | -0.01% / -0.19% | -0.02 / -0.55 | -0.022 (-1.5) | -0.6 / -0.3 / -2.0 / -1.4 | +0.13% (0.3) | 25% |
| A9 coskewness | -0.02% / -0.01% | -0.11 / -0.02 | +0.001 (0.1) | -0.1 / -0.2 / -0.2 / +0.9 | +0.31% (1.1) | 31% |
| A10 Amihud illiquidity | -0.07% / -0.26% | -0.26 / -0.91 | -0.017 (-1.4) | +0.4 / +0.1 / -1.1 / -1.7 | +0.57% (1.6) | 58% |
| **A11 volume trend (long falling volume)** | **-0.61% / -0.52%** | **-2.31 / -2.42** | -0.009 (-0.9) | +0.0 / -0.8 / -0.4 / -0.9 | -0.59% (-1.8) | 39% |
| A12 zero-return days | -0.36% / +0.30% | -1.22 / +1.02 | -0.017 (-1.6) | -0.7 / -0.9 / -1.5 / -1.5 | +0.13% (0.4) | 25% |
| **A13 illiquidity shock (long rising illiquidity)** | **-0.78% / -0.72%** | **-3.47 / -2.86** | -0.015 (-1.5) | +0.4 / -1.5 / -1.1 / -1.4 | **-0.90% (-2.7)** | 54% |

Calendar and event (Nifty timing alpha at 15:15; A16 one event a year, 11 years):

| anomaly | 2016-21 / 2022-26 | t |
|---|---|---|
| A14 day of week (flat over Mondays) | +0.61% / -0.09% a month | +1.22 / -0.40 |
| A15 Halloween (Nov-Apr only) | -0.05% / -0.06% a month | -0.14 / -0.24 |
| A16 India year-end losers, late March to April | -2.37% / -0.27% a year | -1.40 / -0.31 |

## Family-level validation (new: from the public validation toolkits)

Over the 15 monthly series, aligned from 2018-02 (104 months):

* **White's Reality Check p = 0.52, Hansen's SPA p = 0.68** - the best of the family (A1, t +1.30 over this window) is
  not better than luck once the search over 15 is counted.
* **PBO = 0.33** over 12,870 combinatorial splits, and the in-sample-to-out-of-sample Sharpe slope is **-0.61**: the
  anomaly that looks best in one half of the months tends to look *worse* in the other. (A single family's PBO is
  noisy - on pure noise it ranges 0.15-0.6 - so it is read beside SPA, not alone.)

## Predictions

* **P1 right**: ivol, MAX and skew order names in the predicted direction in every tier, IC rising toward the small
  tier - and, like low volatility, the ordering does not pay long-only (net negative).
* **P2 right**: none passes.
* **P3 right, and more than predicted**: the liquidity anomalies are nothing (A10, A12) or significantly **reversed**
  (A11, A13).
* **P4 half right**: SPA does not reject; PBO is 0.33, not above 0.5.

## The finding: rising volume and improving liquidity outperform

A11 and A13 are one observation seen twice: names whose rupee volume is rising, and whose price impact per rupee is
falling, beat names losing volume - in both halves, at t -2.3 to -3.5 for the registered (losing) side, and -0.90% a
month gross between the extremes of A13. Read the right way round it is the **high-volume return premium**
(Gervais, Kaniel & Mingelgrin 2001: stocks with unusually high trading volume subsequently rise, through visibility)
- a published effect, but **found here as the sign-flip of registered hypotheses, in-sample**, which is the oldest way
to manufacture a result. It therefore earns no verdict. It is registered out of sample and as a held-out replication
on names this test never touched: [[PRE-REGISTERED high-volume return premium]].

Also visible: intermediate momentum (A1) and seasonality (A3) carry real long-short spreads (t 3.3 and 2.6 gross) that
long-only books with delivery costs cannot harvest - the momentum family's familiar shape.

Every anomaly is on paper in the zoo with this verdict.

Information, not advice.
