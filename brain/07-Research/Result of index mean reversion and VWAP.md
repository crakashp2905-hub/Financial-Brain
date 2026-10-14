---
type: research
tags:
  - research
  - result
  - index
  - technical
date: 2026-10-14
---

# Result of index mean reversion and VWAP

Tests [[PRE-REGISTERED index mean reversion and VWAP]] (rules from the owner's sources: QuantPedia, Tradezella)
under [[ADR-0004 one evidence standard]], trial 9 of 2026Q4 (budget raised 8 -> 9 by the owner, amendment 1).
Nifty at 15:15 from minute bars, 2,659 sessions 2016-01-01 to 2026-10-08. Code `evaluation/index_rules.py`;
results `data/edges/b4_result.json`.

## Verdict: FAIL - 0 of 9

Timing alpha (position minus its own trailing exposure, times the next 15:15-to-15:15 return over cash, after 2 bp a
side), summed by month:

| rule | time in market | per month 2016-21 / 2022-26 | t 2016-21 / 2022-26 |
|---|---:|---|---|
| Q1 long after an extreme down day | 9% | +0.06% / +0.05% | +0.27 / +0.30 |
| Q2 short after an extreme up day | 8% | +0.04% / -0.09% | +0.15 / -0.54 |
| Q3 RSI long (<35 in, >55 out) | 22% | -0.30% / +0.05% | -0.71 / +0.18 |
| Q4 RSI short (>65 in, <55 out) | 33% | -0.23% / +0.15% | -0.99 / +0.71 |
| Q5 long above MA10 | 59% | -0.00% / +0.12% | -0.01 / +0.50 |
| Q6 long a dip below MA10, out at a 10-day high | 54% | -0.06% / -0.21% | -0.20 / -0.89 |
| Q7 cross above MA10, three sessions | 24% | +0.21% / +0.01% | +1.03 / +0.10 |
| Q8 10-day low long / 10-day high short | 100% | -0.12% / -0.31% | -0.17 / -0.59 |
| V1 VWAP bounce, intraday (1,900 trades, 45% long) | - | -0.18% / +0.10% | -0.55 / +0.36 |

Mean V1 trade: -0.4 bp after 4 bp of costs - the bounce off VWAP carries no direction on the index.

## Predictions

* **R1 wrong**: Q1 is positive in both halves but tiny (+0.05% a month); Q3 is negative in 2016-21. Neither is near.
* **R2 mostly right**: the short rules do not pay (Q2 and Q4 negative in one half each; Q8 negative in both).
* **R3 right**: V1 fails.

## What it means

QuantPedia's SPY rules do not carry over to the Nifty once the market's own drift is taken out: what looks like an
edge on an index chart is mostly being long a rising index. Short-horizon index timing on daily or intraday price
alone is now closed here, alongside trend timing ([[Result of market timing]]), overnight, opening fade,
turn-of-month and pre-holiday. All nine stay on paper in the zoo with this verdict.

Information, not advice.
