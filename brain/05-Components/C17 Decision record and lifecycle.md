---
type: component
phase: 2
status: done
resources: []
depends-on:
  - "[[C09 Evidence ledger]]"
  - "[[C16 Investment Committee]]"
tags:
  - component
  - phase/2
---

# C17 Decision record and lifecycle

**Phase 2** · Immutable structured decisions

`DRAFT -> EVIDENCE VERIFIED -> RISK REVIEWED -> PAPER CANDIDATE -> HUMAN-APPROVED
-> PROPOSED TO BROKER -> EXECUTED -> OUTCOME MEASURED -> POSTMORTEM COMPLETE`

Only the human-approval policy advances a live proposal. See [[Decision contract]].

## Resources needed
- _none_

## Depends on
- [[C09 Evidence ledger]]
- [[C16 Investment Committee]]

---
[[MOC Build]]

> [!success] Built, and now monitored (2026-09-20)
> Decisions carry **typed invalidation checks** beside the prose, re-evaluated nightly
> over Tier-1 data (`fb monitor`): drawdown from entry, a red-flag filing, an accepted
> adverse reading, price levels, pledges. A trigger becomes dated evidence and a decision
> event; prose with no typed twin is reported **unmonitored** rather than assumed
> satisfied. The brief carries a "Theses under watch" section.
