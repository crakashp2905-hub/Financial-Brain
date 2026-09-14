---
type: concept
tags:
  - concept
  - principle
  - learning
---

# Never learn by self-modification

❌ trade loses -> LLM rewrites its prompt -> trade again

✅ trade loses -> record decision -> determine *why* -> update performance statistics ->
test an alternative hypothesis -> backtest -> paper trade -> promote only if validated

The agent develops **empirical memory**, not uncontrolled self-modification. The
adaptation loop supports rollback, preserves old versions, and never makes a silent
prompt, model, score or live-rule change. See [[C25 Outcome attribution and calibration]].
