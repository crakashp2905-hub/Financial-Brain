---
type: research
tags:
  - research
  - pre-registration
  - fundamentals
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED fundamentals factors

Written and committed before the point-in-time fundamentals panel is built or scored. The first time this
project can test fundamentals at all: [[Result of the promoter graph and pledging tests]] recorded it as
"absent, not weak". The data is exchange XBRL (`ingest/xbrl_results.py`), `filed_at` = the exchange's
dissemination time, so a factor at session t uses only filings public on or before t.

## Coverage, stated first

XBRL has been pulled for the **300 most liquid names**, quarters ended December 2018 to December 2024
(NSE moved later results to an "Integrated Filing" feed not yet covered). So: sessions from 2019-06 to
2025-03 (a factor needs four quarters), tiers `large` (1-50), `next` (51-100), `mid` (101-250) and a thin
`small` (251-300). **There is no held-out sample yet.** A second XBRL pass over ranks 301-1000 is the
holdout: anything that replicates here is a hypothesis for that pass, not a finding.

## Factors, built from quarterly (non-cumulative) figures, consolidated basis where the name files it

    ep          TTM profit after tax / market cap       market cap = close_raw x (paid-up capital / face value)
    sue         (EPS_q - EPS_q-4) / sd of that change over the previous 6 quarters (min 4)   [PEAD]
    margin      TTM profit after tax / TTM revenue
    rev_growth  TTM revenue / TTM revenue a year earlier - 1

Directions fixed in advance: high `ep` good, high `sue` good, high `margin` good, high `rev_growth` good.
Outcome: market-adjusted forward return over h = 20 and 60 sessions from monthly sessions. Eight trials
(4 factors x 2 horizons) on top of the running ledger; bar about 3.74.

## Predictions

* **F1 ep** positive IC at h=60 but **fails to replicate** (same sign at |t|>=2 in fewer than 3 of 4 tiers):
  the Indian value premium is small against the size and low-volatility effects already found.
* **F2 sue** (post-earnings drift) positive at h=20 **and replicates** in at least 3 tiers: the one
  fundamentals effect I expect to survive, because it is a delayed reaction to a dated public number.
* **F3 margin** has IC of the wrong sign or indistinguishable from zero at h=60: high-margin names are
  already expensive and already high-momentum.
* **F4 rev_growth** is null (|t| < 2) at both horizons.
* **F5 distinctness**: the rank correlation of `ep`, `margin` and `sue` with `mom_12_1` is below 0.4.
* **F6 none of the four clears the Bonferroni bar** in the pooled sample except possibly `sue`.

Pass criteria as written. No factor, horizon, tier edge or definition is changed after seeing a result.
