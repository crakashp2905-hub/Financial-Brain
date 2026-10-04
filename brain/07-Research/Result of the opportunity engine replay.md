---
type: research
tags:
  - research
  - opportunity-engine
  - replay
date: 2026-10-04
status: result
---

# Result of the opportunity engine replay

Predictions committed at `30e4e8e` before running. 129 monthly sessions, 2016-04 to 2026-08, about 128,000
(session, name) decisions, every one written to opportunity memory and resolved at 20 sessions.
**Not out-of-sample in time**: the four signals were found on this period; only the cross-sectional
holdout was clean. This replay tests cost, capacity and calibration, not edge.

    R1  trades in < 10% of sessions              PASS   0.8% (1 trade in 129 sessions)
    R2  realised IC +0.01..+0.05, t < 3.7        FAIL   IC +0.083, t = +5.55
    R3  top-20 gross > 0, net within +-0.25%     FAIL   gross -0.15%/mo (t -0.37), net -0.52%/mo (t -1.30)
    R4  naive mom_12_1 >= composite, net         PASS   naive -0.11%/mo vs composite -0.52%/mo
    R5  stated alpha overstates realised         PASS   calibration slope 0.71
    R6  event-blocked: bigger moves, same mean   PART   |excess| 8.5% vs 7.8% yes; mean -0.25% vs +0.03%

## What it says

**The engine is a refusal machine at the assumed IC, and the assumed IC was wrong.** At IC 0.02 it opened
one position in ten years. But the realised composite IC is 0.083 at t = 5.5, four times the assumption
and above the Bonferroni bar. I under-estimated the signal and, with the cost hurdle fixed in advance,
that made the engine trade almost never. I did not re-tune the IC afterwards: the replay result stands as
registered, and the better IC is a *new* hypothesis that needs fresh data (see live paper trading).

**High IC, no top-20 profit: the edge is in avoiding the bottom, not picking the top.** Mean 20-session
excess by composite decile, from opportunity memory (descriptive, not registered):

    decile    1      2      3      4      5      6      7      8      9     10
    %/mo   -1.13  -0.62  -0.11  +0.11  +0.20  +0.33  +0.18  +0.42  +0.39  +0.25
    t      -3.49  -2.88  -0.64  +0.99  +1.66  +3.06  +1.60  +2.68  +2.08  +0.85

The bottom two deciles lose 0.6-1.1% a month and the top decile is no better than deciles 8-9. The
composite is a **"do not own this" filter** far more than a "buy this" list. That is consistent with the
pre-registered finding that costs consume gross alpha, and it points at where a long-only book could use
it: exclude the worst 20% and hold the rest at low cost, rather than concentrate in the top 20.

**Calibration.** The stated alpha overstates realised excess (slope 0.71), as predicted, so Grinold's form
should be shrunk, not trusted.

**Rejection audit.** Names rejected for cost had mean excess +0.28%/month (win rate 46%), those rejected
for no alpha -0.27% (41%), those blocked by a live critical filing -0.25% (43%). The cost rule discards
names that did slightly better than average: gross alpha that cannot be captured at the stated costs,
mostly in smaller names. The event block removes names that did worse. Neither rule is shown to be
destroying value; the cost rule is giving up gross return deliberately.

## What this does not settle

* The 1-trade book makes `rejection_edge` meaningless as a tradeable statistic.
* Nothing here can pass the Money Gate: the period is in-sample for the signals. The decile profile is
  the first thing to confirm on live paper decisions (`paper-daily`), which are the only evidence recorded
  before their outcomes.

Related: [[PRE-REGISTERED opportunity engine replay]] · [[Naive momentum beats it, and the horizon was a fit]]
