---
type: research
tags:
  - research
  - pre-registration
  - signal-half-life
  - market-graph
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED signal half-life and second-order spillover

Written and committed before either study runs.

## A. Signal half-life

Question: how long does each signal's information last, which sets how often a book on it should turn
over. Monthly sessions (every 20th), 2016-2026, liquid universe (turnover >= Rs 1 cr/day). For each of
`vol_60` (low is good), `mom_12_1`, `dist_52w_high`, `ret_20d` (reversal) and the equal-weight composite,
the Spearman IC between the signal at t and the market-adjusted forward return over h = 1, 5, 10, 20, 40,
60, 120 sessions. Half-life is the horizon past the IC peak at which IC falls to half the peak, linearly
interpolated; if it has not halved by 120 it is reported as ">120".

Predictions:
* **H1** `ret_20d` reversal peaks at h <= 10 and has a half-life <= 40 sessions.
* **H2** `mom_12_1` and `dist_52w_high` peak at h >= 40 and have a half-life > 120 (still rising or flat).
* **H3** `vol_60` is the most persistent: peak at h >= 60, no halving by 120.
* **H4** the composite peaks between h = 20 and 60.
* **H5** at h = 1 no signal has |IC| above 0.02: a day is too short for these to matter.

## B. Second-order spillover through the dated promoter graph

Question: when a company reports results and its stock reacts, do promoter-group siblings (dated links,
promoter filers only) continue in the same direction? Event: a `RESULTS` filing for A; signal: A's
reaction `ar_react` (already public by the first close); outcome: the sibling's market-adjusted return
over the next 5 sessions from that same entry close. Effect = mean over events of
`sign(ar_react) x sibling AR5`, siblings averaged within event, one observation per event, standard
errors clustered by entry date. Placebo: the same calculation pairing each event with the siblings of a
*different* randomly drawn event of the same year. Three trials (h = 1, 5, 20 sessions); bar is the
running Bonferroni bar (about 3.7).

Predictions:
* **S1** the 5-day spillover is positive but small (< 20 bp) and does **not** clear the bar.
* **S2** the placebo is indistinguishable from zero, so any raw effect is not a market-wide artifact.
* **S3** the 1-day spillover is larger than the 20-day one (information diffuses fast, then fades).

Pass criteria as written; no event type, horizon, link filter or window is added afterwards.
