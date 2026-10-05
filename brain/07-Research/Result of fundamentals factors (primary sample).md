---
type: research
tags:
  - research
  - fundamentals
  - point-in-time
date: 2026-10-05
status: result
---

# Result of fundamentals factors (primary sample, ranks 1-300)

Predictions committed at `6f1995c`, amended at `344f8dd` before any scoring (point-in-time 2019 universe). First
test of fundamentals in this project: previously "absent, not weak". Data: exchange XBRL, `filed_at` = the exchange's
dissemination time, restatements as new rows, consolidated basis where filed, **quarters ended Dec 2018 to Dec 2024**
(NSE moved later results to "Integrated Filing", not covered).

## Sample

The 300 most liquid names by median turnover in H1 2019: 288 in the panel, 12,691 quarterly filings, **67 monthly
sessions** (every 20th, 2019-10 to 2025-03). Factors start at different dates because they need different history:
`ep` and `margin` from 2019-10 (4 quarters), `rev_growth` from 2020-10 (8), `sue` from 2021-01 (9; about 52
sessions). Median names scored per session: ep 254, margin 269, rev_growth 184, sue 167.

## One deviation from the framework, recorded

`evaluation/replication` requires 100 sessions per tier, a floor set for daily sessions. A monthly panel has 67, so
every tier was "unmeasurable". The registration specified monthly sessions (non-overlapping 20-session windows, which
is the cleaner design), so the floor was lowered to **40 for this study only**. Nothing else in the test changed.

## Results: cross-sectional rank IC by liquidity tier (IC / t), direction fixed in advance

    factor       h     large          next           mid          small        tiers at |t|>=2 same sign   pooled IC
    ep          20   +0.030/ +1.0   +0.030/ +1.0   +0.010/ +0.5  +0.034/ +1.7        0                      +0.026
    ep          60   +0.040/ +1.4   +0.040/ +1.5   +0.031/ +1.5  +0.071/ +3.4        1                      +0.046
    sue         20   -0.004/ -0.1   +0.050/ +2.0   +0.023/ +1.2  +0.030/ +1.2        0                      +0.024
    sue         60   -0.032/ -1.3   +0.075/ +2.8   +0.008/ +0.4  +0.031/ +1.2        1                      +0.020
    margin      20   +0.013/ +0.6   -0.008/ -0.3   +0.012/ +0.7  +0.005/ +0.2        0                      +0.006
    margin      60   -0.003/ -0.1   -0.011/ -0.4   -0.009/ -0.5  +0.011/ +0.5        0                      -0.003
    rev_growth  20   -0.003/ -0.1   -0.056/ -1.7   -0.024/ -1.1  +0.029/ +1.3        0                      -0.013
    rev_growth  60   -0.001/ -0.0   -0.102/ -3.2   +0.005/ +0.2  +0.028/ +1.1        0 (1 opposite)         -0.018

Bonferroni bar over the running ledger plus these eight trials: **3.755**. The largest t anywhere is 3.4.

Distinctness (mean rank correlation with the known families): ep -0.05 with momentum, sue +0.22, margin +0.10,
rev_growth +0.16, and all |rho| < 0.25 against `dist_52w_high` and `vol_60`. These are not momentum in disguise.

## Scorecard

    F1 ep positive at h=60 but fails to replicate             PASS    positive in all four tiers (+0.031 to +0.071); one tier at t 3.4, none else >= 2
    F2 sue positive at h=20 and replicates in >=3 tiers       FAIL    positive pooled (+0.024) but 0 tiers clear |t|>=2; the next-50 tier is the only one near it (+0.050, t 2.0), the large tier is flat
    F3 margin wrong sign or ~0 at h=60                        PASS    pooled IC -0.003
    F4 rev_growth null (|t| < 2) at both horizons             MOSTLY  null in 7 of 8 cells; the next-50 tier is significantly NEGATIVE at h=60 (-0.102, t -3.2)
    F5 ep, margin, sue |rho| with momentum < 0.4              PASS
    F6 none clears the Bonferroni bar (sue possibly)          PASS    nothing does; the best is ep in the small tier at h=60, t 3.4

## What it says

**The post-earnings-drift effect I expected to be the survivor did not replicate.** SUE is positive on average
(+0.02) but it is not there in the large caps and clears 2 in only one tier, on about 52 sessions. This
is a power problem as much as a finding: 167 names a session and 52 sessions are not much to detect an IC of 0.02.

**Earnings yield is the one faint hint.** Positive in every tier at both horizons, +0.026 at h=20 and +0.046 at h=60,
rising with the horizon the way the other durable signals do (value is a slow signal) and uncorrelated with
momentum. Nothing here clears a multiple-testing bar, and one consistent sign across four correlated tiers is
weaker evidence than it looks. It is the only candidate worth the holdout.

**Margin and growth are not signals here.** Profitability does nothing; faster-growing revenue, if anything,
does worse (negative in the middle tiers), which is the usual "growth is priced" result.

## What this does not settle

* **Power.** 67 sessions, 50-name large tier. A true IC of 0.03 would often miss here.
* **Coverage ends in Dec 2024**; the last ten months are not in the test.
* **Survivorship the other way**: delisted names whose results NSE no longer lists are absent.
* **No fresh data yet.** The pre-registered holdout is ranks 301-700 of the same 2019 universe, scored once,
  after this note. Whether `ep` carries to them is the real test.

Related: [[PRE-REGISTERED fundamentals factors]] · [[Result of the promoter graph and pledging tests]] ·
[[Result of half-life and second-order spillover]]
