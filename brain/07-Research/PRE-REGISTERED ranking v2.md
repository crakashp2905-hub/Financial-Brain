---
type: research
tags:
  - research
  - pre-registration
  - ranking
date: 2026-10-06
status: pre-registered
---

# PRE-REGISTERED ranking v2

Written before it is computed. The owner asked for the ranking fix flagged in
[[Signal horizons for a trader (half-life table)]]: ranking v1 gives every name in a composite decile the same
expected return and orders the ties by cost and risk, which turned book-v1 into a large-cap bet
([[Result of the suggestion paper books]]) and filled the 2026-10-06 top 10 with low-volatility names whose top
quintile under-earns long-only.

## What v2 is (fixed here, no parameter chosen by looking at results)

* **Score** = the mean of the rank-normal scores of **12-1 momentum** and **nearness to the 52-week high** - the two
  signals that paid long-only at every horizon in the half-life table. Low volatility and reversal are dropped from
  the long-only ranking (they rank well only through the bottom of the cross-section).
* **Ranking** = by score alone, continuous, so there are no ties to break; cost never orders names.
* **Expected excess** at horizon h = b_h x score, with b_h the slope of forward excess on score, **estimated on
  2016-2021 only** and frozen. Expected net alpha = that minus the itemised costs (decisions/net_alpha). A name is
  listed only if its expected net is positive.
* Universe: the liquid cross-section (20-day turnover >= Rs 1 crore), as the champion uses.

## Historical comparison (the screen, not the verdict)

Monthly (every 20 sessions), 2016-2026. Equal-weight books of the **top 20** and **top 50**, held 20 sessions,
against the equal-weighted liquid universe. Net of costs: the share of the book replaced each month x the sized
round trip of a Rs 50,000 order in each name (fees, spread, impact). One observation per month, t across months.

* v1: today's ranking (best expected net per name), top N
* **v2**: as above, top N
* champion: naive 12-1 momentum, top N
* calibration 2016-2021; **test 2022-01 to 2026-08**

## Pass criteria (test period, top 20 and top 50 both)

* **R1** v2's net excess over the universe is positive with t >= 2.
* **R2** v2 beats v1: the monthly difference v2 - v1 is positive with t >= 2.
* **R3** v2 is not worse than the champion: v2 - champion > -0.10% a month.

If R1-R3 hold, v2 replaces v1 on the trader view and in the books (as new versions: book-v2, swing-v2), and joins the
locked champion competition as a challenger. If any fails, v1 stays, the terminal keeps its warning, and v2 joins the
competition anyway - the live record decides.

## Predictions

* **P1** R2 passes clearly: v1's large-cap tie-break costs it against any momentum-led book.
* **P2** v2 and the champion are within 0.10% a month of each other (R3 passes narrowly); the 52-week-high term adds
  little over momentum alone.
* **P3** v2's test-period net excess is positive but under 0.6% a month, and does not clear the Bonferroni bar.

Caveat, stated now: the half-life table that motivated v2 used all of 2016-2026, so the test period is not clean for
the *idea*; it is clean only for the frozen slope. The live competition from 2026-10-07 is the clean test.
