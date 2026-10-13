---
type: research
tags:
  - research
  - pre-registration
  - momentum
date: 2026-10-13
status: pre-registered
---

# PRE-REGISTERED cost-aware momentum book

Written on 2026-10-13 under [[ADR-0004 one evidence standard]], before anything below is computed. Trial M1 of
2026Q4's budget.

## Why this, and why it is not a search

Every momentum-family result here has the same shape: the signal is real (12-1 momentum and 52-week-high proximity
replicate in all four size tiers, joint p 1e-23 and 1e-12, with the IC rising from large to small caps -
[[Four edges that replicate, and the chart patterns that do work]]) and the strategy misses the bar on cost and
noise (plain momentum +1.35%/month, t 2.15, then +1.06%, t 1.65; E1 ensemble t +0.7 / -0.1). The h = 20 cost
hurdle is 4 bp a session against 1-10 bp of alpha. Three things not yet combined attack exactly that:

1. **A longer hold**: 40 sessions, where `mom_12_1` was net positive and peaked in the horizon sweep ([[h20]]). That
   sweep was a search over nine horizons on the whole history; the choice is search-informed and stated as such.
   G5 (out of sample) is what answers for it.
2. **A no-trade band**: buy into the top 40, sell only on falling out of the top 100. Under proportional costs the
   optimal policy tolerates a region around the target (Constantinides 1986). The signal barely reorders month to
   month, so the band should cut turnover without changing what is held much.
3. **The N2 exclusion**: names whose results the market rejected lag by 1.3-2.1% over 20 sessions
   ([[Result of edges for every mode]]); a momentum book does not want to hold them.

Plus inverse-volatility weights: low volatility orders names (IC t +12) but earns no alpha at market beta, so it is
used to spread risk, not to pick.

## The book, fixed

* **Universe**: each rebalance date, the 500 most liquid names by 20-session turnover (`evaluation/edges.load`).
* **Rebalance**: every 40 sessions from the first session of 2016.
* **Score**: the mean of two cross-sectional percentile ranks within the universe: 12-1 momentum
  (close 21 sessions ago / close 252 sessions ago - 1) and nearness to the 52-week high (close / max close of the
  last 252 sessions). Names without 252 sessions of history are not scored.
* **N2 exclusion**: a RESULTS reaction (close before the announcement to the close of the session after, minus the
  equal-weighted universe) in the bottom fifth of the reactions whose windows closed in the previous 30 days
  (at least 5 peers) flags the name for 20 sessions from that close. This is the **live** definition in
  `decisions/news_filter.py`; the test's within-month quintile would look ahead.
* **Holdings**: keep a held name while it ranks in the top 100 of eligible (unflagged, scored) names; fill to 40
  from the top of the ranking.
* **Weights**: inverse of each name's 60-session daily return volatility, normalised to 1, capped at 5% (excess
  redistributed pro rata).
* **Costs**: each name's delivery round trip by liquidity bucket, as in every edge test (25 / 30 / 45 / 60 bp for
  mega / large / mid / small by adv20), half charged on each unit of weight bought or sold, against drifted weights.
* **Measured**: net excess per 40-session period against the equal-weighted universe.

## The bar (ADR-0004)

* G3: net excess mean positive with t >= 2 in 2016-2021 **and** in 2022-2026.
* G4: min(t_a, t_b) above the 95th percentile of 200 random books with the same number of names, the same number
  replaced at each rebalance, the same weighting and the same eligible universe (N2-flagged names excluded).
* Reported, not gating: Sharpe of the net excess, turnover per rebalance, the Bonferroni bar of the firewall ledger.

## Predictions

* **M1a** net excess is positive in both halves.
* **M1b** G3 fails in 2022-2026 (t < 2) - plain momentum's monthly t there was 1.65 and fewer, longer periods do
  not obviously help. I put the chance of passing G3 at about 30%.
* **M1c** turnover per rebalance is below 30% of the book.
* **M1d** the N2 exclusion changes the period mean by less than 0.2 percentage points (it touches few names).

## Out of sample (G5), fixed now

Data cutoff: 2026-10-12. Pass or fail, the book is paper-traded in the zoo from the next session as
`edge_m1_momentum_book`. G5 needs 12 rebalances after the cutoff (about 23 months at 40 sessions) with a positive
net mean. Early stop: at each month end, the live cumulative net excess is compared with the backtest's 2022-26
per-session distribution (90% band, mean·k ± 1.645·sd·√k at k sessions); **three consecutive month ends below the
lower band fail it early**. Nothing about the book changes because of its paper record.

Information, not advice.
