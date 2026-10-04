---
type: research
tags:
  - research
  - pre-registration
  - opportunity-engine
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED opportunity engine replay

Written and committed before the replay runs. `opportunity/engine.py` ranks every liquid name by net
expected alpha (`IC x sigma x z - sized round-trip cost`) from an equal-weight composite of four signals
already pre-registered and held-out: low `vol_60`, `mom_12_1`, `dist_52w_high`, and 20-day reversal.
`IC` is **assumed at 0.02**, fixed here, not tuned.

## What this replay is, and is not

Monthly sessions (every 20th) 2016-04 to 2026-08, 20-session forward windows. **It is not out-of-sample
in time**: the four signals were found with data through 2026, and only their *cross-sectional* holdout
(ranks 501-1500) was clean. A positive replay therefore cannot clear the Money Gate; only live paper
trading can. What the replay can do is expose cost, capacity and calibration failures.

On the first real scan (2026-09-18) the engine already trades nothing: the best net alpha is +0.17%
against a 0.5% hurdle. The replay asks whether that is typical.

## Predictions

* **R1** The engine opens at least one TRADE in fewer than **10%** of monthly sessions. Costs average
  ~50 bp round trip against alpha of a few tens of bp at IC 0.02.
* **R2** The realised composite IC (Spearman, 20-session, non-overlapping) is **+0.01 to +0.05**, so the
  assumed 0.02 is the right order of magnitude, and its t is below the Bonferroni bar of 3.70.
* **R3** The top-20 by composite, ignoring the net-alpha hurdle, earns positive gross excess over the
  equal-weight universe but **net excess within +-0.25% per month** after sized costs at full monthly
  turnover.
* **R4** Naive top-20 by `mom_12_1` alone does at least as well net as the composite.
* **R5** The stated alpha overstates what is realised: the pooled slope of forward excess on stated alpha
  is **below 1**.
* **R6** Names blocked by a live critical filing (`REJECT_EVENT`) have a larger mean absolute forward
  excess than the rest, but **not** a worse mean: the rule removes variance, not return.

Failures are reported as failures. The IC, the hurdle, the top-N and the horizon are not revisited.
