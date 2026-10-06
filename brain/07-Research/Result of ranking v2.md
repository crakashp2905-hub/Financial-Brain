---
type: research
tags:
  - research
  - result
  - ranking
date: 2026-10-06
status: result
---

# Result of ranking v2

Pre-registration: [[PRE-REGISTERED ranking v2]]. Monthly rebalances (every 20 sessions), 2016-06 to 2026-08, the
liquid cross-section (turnover >= Rs 1 crore), equal-weight top 20 and top 50 held 20 sessions, against the
equal-weighted liquid universe; costs = the share of the book replaced x the sized round trip of a Rs 50,000 order
(fees, spread, impact). One observation per month.

Frozen slopes (2016-2021, 70 dates): **b20 = 0.00727, b60 = 0.02044** per unit of score - now constants in
`opportunity/ranking_v2.py`.

## Net monthly excess over the liquid universe

| period | book | top 20 net (t) | top 50 net (t) | turnover (top 20) |
|---|---|---|---|---|
| calibration 2016-21 | v1 | -0.89% (-1.7) | -0.56% (-1.1) | 65% |
| | **v2** | **+1.65% (+2.6)** | **+1.41% (+3.2)** | 67% |
| | champion | +1.37% (+2.1) | +1.11% (+2.3) | 39% |
| **test 2022-26** | v1 | -0.34% (-0.6) | -0.21% (-0.5) | 61% |
| | **v2** | **+0.01% (0.0)** | **+0.19% (+0.5)** | 71% |
| | champion | -0.31% (-0.6) | -0.14% (-0.3) | 37% |

Differences in the test period: v2 - v1 = +0.35% (t 0.5) and +0.40% (t 0.6) a month; v2 - champion = +0.32% (t 0.6)
and +0.32% (t 0.8).

## Against the criteria

* **R1** (v2 net positive with t >= 2 in the test period): **FAIL** - +0.01% and +0.19%, t 0.0 and 0.5.
* **R2** (v2 beats v1 with t >= 2): **FAIL** - it beats it by 0.35-0.40% a month in both books, in both periods, but
  not significantly in the test period.
* **R3** (v2 not worse than the champion by more than 0.10%): **PASS** - it is ahead by 0.32%.

* **P1** (R2 passes clearly) **wrong.** **P2** (v2 within 0.10% of the champion) **wrong** - ahead by 0.3%, not
  significant. **P3** (positive, under 0.6%, not clearing the bar) **right.**

## What follows, as registered

v2 does **not** replace v1 on the trader view or in the books. v1 stays, with its warning; v2 is shown beside it as
a challenger and joins the locked champion competition from 2026-10-07 (`ranking_v2_top`).

What the numbers say beyond the verdict: v1's ranking lost to the universe in both periods; v2 was ahead of v1 and of
plain momentum in both periods, by a similar margin; and **momentum itself faded after 2021** - the champion is
negative in the test years. So the question for the live record is less "is v2 better than v1" (it has been, in
every split, not significantly) than "is there a long-only edge in this market after costs at all". The live
competition answers that on data no one has seen.

## Calibration of its expected returns

The frozen slope is a straight line in the score. v2's top 20 earned +2.13% a month gross over the universe in
2016-21 (where the slope was fitted) and **+0.51% in 2022-26** - about a third of what the slope implies at those
scores. The terminal says so beside v2's list ("read +5% as roughly +1.5%"); the same overstatement in the tails
that sank the Grinold version of the scanners is present here, smaller.
