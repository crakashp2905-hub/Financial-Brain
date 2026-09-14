---
type: concept
tags:
  - concept
  - contract
---

# Decision contract

Every recommendation is a structured immutable record, never prose:

```
decision_id, instrument, universe, horizon, action, thesis,
supporting_evidence, contrary_evidence, primary_uncertainty,
world_state_version, expected payoff/scenarios, invalidation conditions,
entry/exit logic, sizing/risk budget, portfolio impact, approver,
execution status, outcome window, postmortem status
```

## Lifecycle

```
DRAFT -> EVIDENCE VERIFIED -> RISK REVIEWED -> PAPER CANDIDATE
     -> HUMAN-APPROVED -> PROPOSED TO BROKER -> EXECUTED
     -> OUTCOME MEASURED -> POSTMORTEM COMPLETE
```

**Only the human-approval/execution policy advances a live-trade proposal.** No LLM, web
page, document, social post or agent workflow may bypass this state machine.

Never `BUY — 87%`. Instead: BUY, confidence 72%, with evidence strength, agent agreement,
data quality, regime and social signal rated separately, and the **primary uncertainty
named**. See [[Scores are not predictions]], [[C17 Decision record and lifecycle]].
