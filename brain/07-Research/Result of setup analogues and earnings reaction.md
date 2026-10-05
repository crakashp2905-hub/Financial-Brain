---
type: research
tags:
  - research
  - result
  - analogues
  - earnings
  - learning
date: 2026-10-05
status: result
---

# Result of setup analogues and earnings reaction

Pre-registration: [[PRE-REGISTERED setup analogues and earnings reaction]], committed before either was computed.
Running Bonferroni bar at the time: t = 3.79.

## A. Setup analogues

Pool: 514,507 (name, date) situations, step 5 sessions, only windows closed before the query date. Test: 25 query
dates from 2024 on (every fourth pool date), 120 random names each, the k = 200 analogue mean as the prediction of
the next 5 sessions' excess return over the cross-sectional mean.

| predictor | mean rank IC (5 sessions) | t (one obs per date) |
|---|---|---|
| analogue mean | **+0.053** | +3.11 |
| 12-1 momentum alone | +0.009 | +0.30 |
| difference | +0.044 | |

**A1 is falsified**: the claim was |difference| < 0.01. The analogues carry something momentum does not.

What it is not: proven. t = 3.11 is under the running bar of 3.79, on 25 dates. And the comparison was against
momentum only, while three of the eight features are 1-, 5- and 20-day returns: at a 5-session horizon the most
likely source is **short-term reversal**, which the half-life study already measured (half-life 16 sessions). The
fair next test is analogues against the composite *plus* 5-day reversal; that was not pre-registered and is not
run here. Until then the tool is used as it was built: a description of what similar situations did, shown beside
the whole pool, never as a number that sizes a position.

## B. Earnings reaction by surprise

3,211 results filings with an SUE at the moment of filing (exchange XBRL, quarters to Dec 2024), market-adjusted by
the universe mean. Top-minus-bottom SUE quintile:

| window | Q5 - Q1 | t |
|---|---|---|
| day 0 (previous close to first close after dissemination) | **+1.48%** | +6.43 |
| next 5 sessions | +0.05% | +0.19 |
| next 20 sessions | -0.05% | -0.09 |

* **E1 FAIL.** The reaction rises with surprise, strongly (t 6.4), but the spread is 1.5%, not above 2%. The SUE
  here is a noisy mechanical one (EPS against the same quarter a year earlier, no analyst consensus exists in the
  archive), which flattens the spread.
* **E2 half right.** The 20-session drift is not distinguishable from zero and nowhere near the bar, as predicted;
  it is not *positive*, which the claim also said. There is no post-earnings drift to trade here.
* **E3 PASS.** Day 0 is ~30 times the 5-session drift; the market prices the surprise on the day.

Consequence for the brain: the event scanner keeps `expected_return = 0` after results. A results surprise is
information about the day it lands, which a delivery or swing trade entered after the close cannot capture.

## C. Counterfactual learning (not pre-registered; hypothesis-generating)

`opportunity/learning.systematic_misses` grouped the engine replay's rejections by reason and by quintile of size,
volatility and momentum within each session, one observation per session, and flagged groups at t >= 3.

* Names **rejected for cost** in the top momentum and the smallest size quintiles went on to beat the universe
  *gross* by about +0.5 to +0.7% a month (t 3.2 to 4.4). This is the expected shape: the cost rule rejects exactly
  the small, high-momentum names where momentum is strongest and trading is most expensive. Gross outperformance
  is not a miss unless it survives the cost that caused the rejection; at the measured small-cap round trip it
  mostly does not.
* Names **blocked for a pending event** went on to *underperform* by about -1.5% a month (t -4.0): the block
  avoided losers, and stays.

The number of groups examined is printed beside every flag (about 0.1 would clear t 3 by chance). None of this
changes a rule; a new rule version would be pre-registered first.

## D. One model for expected return (a fix found while building the terminal)

The first terminal ranked opportunities by each scanner's Grinold expectation, IC x sigma x z. On the live cross
section that put 95%- and 130%-volatility micro-caps on top with +5 to +6% expected net over 60 days, while their
own analogues pointed the other way. The linear form extrapolates to extremes; the replay found the top composite
decile earned *less* than deciles 8 and 9. Scanners now take their expected return from one model, the
composite decile's measured 20-session mean scaled by the half-life curve, so a name's scanners cannot disagree
about it and nothing gets a number beyond what a decile actually earned (+0.42% over 20 sessions at most). The top
of the board became large, liquid names at about +0.55% over 60 days against 0.26% cost. The decile table is
in-sample, and the record says so.
