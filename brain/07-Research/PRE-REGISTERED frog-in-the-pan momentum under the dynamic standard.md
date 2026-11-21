---
type: research
tags:
  - research
  - pre-registration
  - out-of-sample
  - momentum
date: 2026-11-16
status: pre-registered (out of sample only)
---

# PRE-REGISTERED frog-in-the-pan momentum under the dynamic standard

Written on 2026-11-16, the day the owner accepted ADR-0007 (`docs/adr/0007-dynamic-evidence-standard.md`) and asked
for this book to be registered under it. The book was **already seen in sample**: it is P3 of
[[PRE-REGISTERED edges for every mode]], which failed ADR-0004's G3 (half t 3.33 / 1.67), and it was re-scored on
2026-11-16 (`docs/trials/dynamic/RESULTS-2026-11-16.md`). So nothing in sample can promote it further. This
registration freezes the rule and the in-sample grade, and says how data after the cutoff will be judged. It spends
no ADR-0004 trial.

## The rule (unchanged from P3; nothing is tuned)

* Universe: the 500 most liquid names by trailing 20-session turnover on the rebalance date.
* Signal: 12-1 momentum, restricted to names at or below the universe median of information discreteness,
  sign(12-1 return) x (share of down days - share of up days) over the 12-1 window (Da, Gurun & Warachka 2014).
* Book: the top 20, equal weight, rebalanced at each month end, held to the next (about 21 sessions).
* Costs: each name's round trip by liquidity bucket on the weight changed (`evaluation/edges.py`).
* Unit: one month. Measure: net excess over the equal-weight 500 held over the same month.
* Paper record: zoo strategy `edge_p3_frog_in_pan` (live since 2026-10-08), as it runs today.

## ADR-0007 fields, fixed here

* **Prior class A**: momentum is published across markets, has a behavioural mechanism, and both `mom_12_1` and
  `dist_52w_high` replicate in all four liquidity tiers here ([[Four edges that replicate, and the chart patterns that do work]]).
* **Family 20**: P0-P3, E1, W2, W3, M1, A1 and the momentum columns of the horizon sweep. Any further momentum
  variant examined raises this number for every member.
* **Direction** +1. **Split** 2022-01-01. **Hold** 21 sessions.

## The in-sample grade, frozen (`scripts/research/momentum_excess.py`, data through 2026-11-16)

128 months, 2016-01 to 2026-09: net excess +1.44% a month, cost 0.20%, turnover 42%; t 3.30, Newey-West t 3.15;
halves 3.33 / 1.35; random-book null 95th percentile 1.07 (passed). Family-adjusted z 2.14, posterior 0.77:
**CANDIDATE_SMALL**. The series is `docs/trials/dynamic/momentum-excess-series.parquet`, column
`P3 frog in the pan`.

## How data after 2026-11-16 is judged

1. `fb oos` (ADR-0004 G5, unchanged): 12 month-end rebalances after the cutoff with a positive net excess mean and
   a cumulative result inside the backtest's 90% band is CONFIRMED; three consecutive month ends below the band is
   FAILED_EARLY.
2. ADR-0007's running grade: the live record's z is combined with the frozen backtest z by
   `dynamic_standard.judge(..., live=...)`. A live z at or below -1.64 demotes one tier.

## Predictions

* **D1** The live net excess is positive over the first 12 rebalances, and smaller than the backtest's: under
  +1.0% a month.
* **D2** At least one of the first 12 months loses more than 5% against the universe.
* **D3** The 2022-26 weakness continues: the running posterior does not reach 0.90 within 12 rebalances.

## What it does not do

It does not make the book tradeable. The broker path still reads ADR-0004 and ADR-0006, and letting an ADR-0007
tier reach stage TICKET is a separate owner decision. Known limits, stated before the forward record exists: the
book is the best of about twenty variants; the excess has drawn down 26% against the universe; the depository
charge on each sale is not in the proportional cost model and is material below a few lakh of capital; every gain
is short-term for tax.

Information, not advice.
