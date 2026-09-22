---
type: strategy
tags:
  - strategy
  - tested
verdict: REJECT
---

# Timing model

**Claim.** A logistic model over eight ranked features says when a name will beat its cross-section.

**How it was built here.** Fitted on an expanding window, scored point in time, threshold calibrated the way [[Model router]] calibrates one.

**Verdict.** REJECT - and the interesting part is *why*: see [[Beating the median is not an edge]]. Against a buyable benchmark it cannot calibrate at all.

**What would change the answer.** A target that is a portfolio return from the start. Any future model here is scored against the equal-weighted universe, never the median.

Related: [[MOC Strategies]] · [[Alpha Validation Firewall]] · [[Procedural memory]]
