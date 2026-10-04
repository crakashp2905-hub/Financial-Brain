---
type: research
tags:
  - research
  - promoter-graph
  - point-in-time
date: 2026-10-04
status: result
---

# Dated promoter membership separates dating from filtering

Follows [[Result of the promoter graph and pledging tests]], which could not claim the strongest signal
here because `promoter_groups` had no dates. `graph/promoter_dated.py` rebuilds membership from
`holder_filings` (every row dated): two companies are linked when the same filer appears in both, the
link is visible from its first filing and lapses 730 days after the last.

## Result, held-out tiers 501-1500, h20, direction fixed

    graph                          links   lineages   group_momentum deep/micro   group_stress deep/micro
    undated (old)                    --        447    +0.027/+8.8   +0.068/+20.5   +0.056/+17.9  +0.081/+23.8
    dated, promoter filers only     993        371    +0.060/+13.7  +0.085/+15.8   +0.091/+19.8  +0.117/+20.3
    dated, ALL filers/relations 122,604      2,464    +0.006/ +4.0  +0.026/+15.0   +0.033/+20.3  +0.047/+23.7

I changed two things at once the first time (dating and filer filtering), so the third row separates them.

* **Dating does not remove the signal.** Point-in-time membership with no filtering still passes at
  t of 4 to 24 (the one weak cell is group_momentum, deep tier, t=4.0). The survivorship worry (sibling momentum = survivor momentum) is not what drives it.
* **Filtering to promoter filers makes it stronger**, roughly 2-10x in IC. The ownership-graph version
  dilutes the promoter signal with institution-linked pairs (LIC, mutual funds).
* **Distinct from known families:** rank correlation with mom_12_1 is +0.31 / +0.25, with dist_52w_high
  +0.26 / +0.35, with vol_60 -0.09 / -0.20. Below the 0.4 threshold registered in advance.
* TTL: lineage count is identical from 365 to 1825 days; only density changes.

## A look-ahead bug the unit test caught

The first build dated a link by `LEAST` of the two filings, making A-B visible when only A had filed.
Nothing evidences the shared filer until B files too, so it must be `GREATEST`. Fixed and re-run; the
table above is the corrected one (first-build numbers were group_momentum +0.080/+0.087, stress
+0.097/+0.098). The bug flattered momentum slightly and the conclusion did not change.

## What this does not establish

* The filter and the TTL were chosen with the data in view. The filter rationale (institutions are not
  promoters) was stated before the run, but this is not a fresh pre-registration. The *dating* test is
  clean; the *promoter-filer* gain is not independently held out.
* Dated links begin only when the archive evidences them, so early sessions are sparse (median 132 names
  per session) - the correct direction to be wrong in, but coverage is uneven across time.
* IC is not P&L. Nothing here has been through costs or the paper engine. The size gradient (stronger in
  micro caps) means much of it may be untradeable.

Verdict: the promoter-graph signal survives the dating defect. It moves from "cannot claim" to
"replicated and point-in-time, cost-untested".
