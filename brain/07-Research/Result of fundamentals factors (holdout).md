---
type: research
tags:
  - research
  - result
  - fundamentals
  - holdout
date: 2026-10-06
status: result
---

# Result of fundamentals factors (holdout, ranks 301-700)

Pre-registration: [[PRE-REGISTERED fundamentals factors]] (with its amendment, committed before any factor was
computed: a point-in-time 2019 universe, ranks 1-300 scored first, ranks 301-700 once afterwards). Primary result:
[[Result of fundamentals factors (primary sample)]].

Data: exchange XBRL, 700 names of the 2019 universe, 28,624 filings stored (679 companies, quarters ended Dec 2018
to Dec 2024), 28,600 passing their arithmetic checks; `filed_at` = exchange dissemination time. Holdout panel: 400
names, 382 with filings; median names per session 246-340 by factor. Same code, same definitions, same deviation
as the primary run (minimum 40 monthly sessions per tier instead of 100 daily). Bonferroni bar 3.755.

| factor | h | mid tier IC / t | small tier IC / t | pooled mean IC | primary pooled |
|---|---|---|---|---|---|
| ep | 20 | +0.015 / +0.5 | +0.036 / +1.8 | +0.026 | +0.026 |
| ep | 60 | +0.025 / +0.9 | +0.032 / +1.4 | +0.029 | +0.046 |
| sue | 20 | +0.007 / +0.2 | +0.054 / +2.5 | +0.033 | +0.024 |
| sue | 60 | -0.016 / -0.4 | **+0.065 / +3.4** | +0.029 | +0.020 |
| margin | 20 | +0.045 / +1.7 | -0.005 / -0.2 | +0.019 | +0.006 |
| margin | 60 | +0.033 / +1.1 | -0.010 / -0.6 | +0.010 | -0.003 |
| rev_growth | 20 | +0.002 / +0.1 | +0.013 / +0.7 | +0.008 | -0.013 |
| rev_growth | 60 | -0.057 / -1.8 | +0.032 / +1.6 | -0.007 | -0.018 |

The large and next tiers are empty by construction: names ranked 301-700 in 2019 seldom reached the top 100 later.
So the pre-registered replication rule (3 of 4 tiers) **cannot be met in this sample** - that is a limitation of the
holdout's design, recorded rather than changed. What can be read is the direction and size.

## Against the predictions

* **F1 ep** (positive, fails to replicate): **holds again.** Positive in both samples and every tier, never above
  t 1.8. A faint value tilt, not a tradeable effect on its own.
* **F2 sue** (positive at h=20 and replicates): **fails as worded**, as in the primary. The holdout's strongest cell
  is sue in the small tier at h=60 (t 3.4), under the bar; the primary's small tier at h=60 was t 1.2. Earnings
  surprise matters most in the less-followed names, consistent with the earnings-reaction result (the market prices
  most of a surprise on the day; what drift there is lives in smaller names).
* **F3 margin** (wrong sign or zero at h=60): **holds** (+0.010).
* **F4 rev_growth** (null): **holds.**
* **F5 distinctness** (correlation with momentum below 0.4): **holds**, though sue (+0.34) and rev_growth (+0.34)
  overlap with momentum more here than in the primary.
* **F6** nothing clears the bar: **holds.**

## What it means for the brain

The review that prompted the fundamentals pull asked whether naive momentum beats the system only because the
system lived in price-and-volume space. Given a fair point-in-time test, quarterly fundamentals add **little**: a
faint value tilt and an earnings-surprise effect in smaller names, neither strong enough to trade alone. They stay
as inputs (the `fundamental_value` scanner keeps expected return 0 and its evidence label moves to
FAINT_BOTH_SAMPLES), and earnings surprise is the one worth a locked out-of-sample run in the small tier.
