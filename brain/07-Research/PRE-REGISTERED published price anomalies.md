---
type: research
tags:
  - research
  - pre-registration
  - anomalies
date: 2026-10-14
status: pre-registered
---

# PRE-REGISTERED published price anomalies

Written on 2026-10-14 under [[ADR-0004 one evidence standard]] (amendment 2: the owner raised 2026Q4's budget to 10
for this trial), before anything below is computed. One trial for the family. The owner asked for "historical
edges", tested "in detail ... with depth".

## The family: every price anomaly in the catalogue never tested here

`research/catalogue.py` lists 29 price-computable anomalies; 12 have been tested (momentum, reversal, 52-week high,
residual momentum, low volatility, betting against beta, turn of month, pre-holiday...). The other 17:

| # | anomaly | paper | signal at each month end | side |
|---|---|---|---|---|
| A1 | intermediate momentum | Novy-Marx (2012) | return from t-12 to t-7 months | high +|
| A2 | long-term reversal | De Bondt & Thaler (1985) | return from t-36 to t-13 months (36, not 60: the panel starts 2015) | low + |
| A3 | return seasonality | Heston & Sadka (2008) | mean return in the same calendar month of each previous year (>= 2 years) | high + |
| A4 | idiosyncratic volatility | Ang, Hodrick, Xing & Zhang (2006) | sd of residuals from a 60-session regression on the Nifty | low + |
| A5 | lottery (MAX) | Bali, Cakici & Whitelaw (2011) | largest daily return of the last 21 sessions | low + |
| A6 | idiosyncratic skewness | Boyer, Mitton & Vorkink (2010) | skewness of those 60-session residuals (realised, a stated proxy) | low + |
| A7 | volatility of volatility | Baltussen, van Bekkum & van der Grient (2018) | sd of rolling 20-session vol over the last 250 sessions / its mean (realised, a stated proxy for implied) | low + |
| A8 | downside beta | Ang, Chen & Xing (2006) | beta on the Nifty over the last 250 sessions, using only days the Nifty fell | high + |
| A9 | coskewness | Harvey & Siddique (2000) | 250-session coskewness with the Nifty | low + |
| A10 | illiquidity | Amihud (2002) | mean of abs(return) / rupee volume over 60 sessions | high + |
| A11 | volume trend | Chordia, Subrahmanyam & Anshuman (2001) | 60-session mean rupee volume / 250-session mean | low + |
| A12 | zero-return days | Lesmond, Ogden & Trzcinka (1999) | share of zero-return sessions in the last 250 | high + |
| A13 | illiquidity shock | Acharya & Pedersen (2005) | Amihud over the last 20 sessions / over the 250 before | high + |
| A14 | day of week | French (1980) | Nifty: flat over Mondays (Friday close to Monday close), long otherwise | timing |
| A15 | Halloween | Bouman & Jacobsen (2002) | Nifty: long November-April, flat May-October | timing |
| A16 | Indian year-end | catalogue (India's Rozeff-Kinney) | long the bottom fifth by April-February return, at the close of the fifth-last March session, to the April month end | event |

**Excluded, stated:** `turnover_low` (Datar, Naik & Radcliffe 1998) needs shares outstanding, which this archive does
not hold - rupee volume alone is the size effect, not turnover.

## Construction

* **Cross-sectional A1-A13**: the 500 most liquid names each month end (`evaluation/edges.load`); **long the
  best fifth** by the signal's side, equal weight, held to the next month end, net of each name's delivery round
  trip by liquidity bucket (25-60 bp, half on each unit of weight changed); measured as net excess over the
  equal-weighted 500. Long-only because that is what can be traded in delivery; the long-short spread is reported.
* **Calendar A14-A15**: Nifty 50 price at 15:15 from minute bars, Nifty futures at 2 bp a side, judged as timing
  alpha net of the rule's own trailing exposure (as [[PRE-REGISTERED index mean reversion and VWAP]]).
* **A16**: one event a year; excess over the equal-weighted 500.

## The bar

* Per anomaly, G3 (both halves, |t| >= 2, right sign) and G4 (same-size, same-turnover random books for A1-A13 and
  A16; circular shifts for A14-A15). Under the null, ~1.7% chance any of 16 passes G3.
* **New for this family, from the public validation toolkits the owner pointed at** (reported beside the verdict,
  and a pass must not be contradicted by them): **Hansen's SPA / White's Reality Check** p-value for the best of the
  family against the benchmark of zero excess (stationary bootstrap), and the **probability of backtest
  overfitting** (Bailey, Borwein, Lopez de Prado & Zhu 2017, combinatorially symmetric cross-validation over the
  family's monthly series).

## Depth (reported, not gating)

For every cross-sectional anomaly: the long-short (top minus bottom fifth) gross spread, the monthly rank IC and its
t, the IC in each of the four size tiers (ranks 1-50, 51-100, 101-250, 251-500), turnover, and the net Sharpe.

## Predictions

* **P1** A4 (ivol), A5 (MAX) and A6 (skewness) order names in the predicted direction in all four tiers - they are
  the volatility family in other coats, and low volatility's ordering is the strongest signal measured here.
* **P2** none of A1-A16 passes G3 long-only after costs (about 80%).
* **P3** the liquidity anomalies (A10, A12, A13) have the wrong sign or nothing within the 500 most liquid names.
* **P4** the family's PBO is above 0.5 and SPA does not reject.

Every anomaly goes into the paper zoo with its verdict.

Information, not advice.
