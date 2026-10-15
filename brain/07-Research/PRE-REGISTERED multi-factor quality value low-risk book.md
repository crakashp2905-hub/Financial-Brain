---
type: research
tags:
  - research
  - pre-registration
  - fundamentals
  - quality
  - value
date: 2026-10-15
status: pre-registered
---

# PRE-REGISTERED multi-factor quality value low-risk book

Written on 2026-10-15 under [[ADR-0004 one evidence standard]] (amendment 4), before anything below is computed. The
owner asked whether the system can trade like Warren Buffett. "Buffett's Alpha" (Frazzini, Kabiller & Pedersen 2018)
found his record largely explained by owning **cheap, safe, high-quality** businesses for a long time (plus leverage,
which is not replicated here). NSE's own factor indices pointed the same way over 2019-2026: quality alone lagged the
Nifty 500, the quality-value-low-volatility blend beat it at lower risk. This tests the blend on the project's own
point-in-time fundamentals, now continuous from the December-2018 quarter (results XBRL to Dec-2024, Integrated
Filing from Mar-2025).

## The book, fixed

* **Fundamentals, point in time**: for each company and quarter the **first-reported** statement (earliest
  `filed_at`), consolidated where filed, else standalone; a figure is usable only from its filing time.
* **Universe**: each month end, the 500 most liquid names (`edges.load`) that have the last four quarters' statements
  known by that date and positive trailing-twelve-month profit (a loss-maker is not a Buffett business).
* **Signals** (equal-weighted mean of cross-sectional percentile ranks; nothing fitted):
  * **profitability**: TTM profit after tax / TTM revenue - higher is better;
  * **earnings stability**: quarters with positive profit among the last eight available (at least four) - higher is
    better;
  * **value**: TTM profit / market capitalisation, with shares = paid-up equity capital / face value from the latest
    known filing and the month-end unadjusted close - higher is better;
  * **low risk**: 60-session daily return volatility - lower is better.
* **Book**: long the best fifth, equal weight, rebalanced monthly, net of each name's delivery round trip by liquidity
  bucket (half on each unit of weight changed); measured as net excess over the equal-weighted eligible universe.
* **Span**: from the first month end with four known quarters (late 2019) to the last complete month.

## The bar

G3: net excess positive with t >= 2 in the months before 2022 **and** from 2022 on (the first half is short - about
26 months - stated); G4: above the 95th percentile of 200 random books with the same size and turnover from the same
eligible names. Reported, not gating: each signal's own top-fifth excess, turnover, Sharpe, and the comparison with
NSE's Alpha Quality Value Low-Volatility 30 index over the same months.

## Predictions

* **B1** positive in both halves (about 65%).
* **B2** fails G3 in the short first half (about 70%).
* **B3** value contributes most, profitability least - India's quality index lagged the market 2019-2026.

Information, not advice.
