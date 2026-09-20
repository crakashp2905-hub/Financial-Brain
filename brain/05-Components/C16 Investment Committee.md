---
type: component
phase: 2
status: done
resources:
  - "[[TradingAgents]]"
depends-on:
  - "[[C08 World state]]"
  - "[[C14 Investment Constitution]]"
tags:
  - component
  - phase/2
---

# C16 Investment Committee

**Phase 2** · Six agents plus orchestrator

Data · Fundamental · Technical · News/Event · Portfolio/Risk · Decision, plus
orchestrator. Bull/Bear debate. **Measure disagreement, don't eliminate it.**
Per-agent track records drive dynamic weighting.

## Resources needed
- [[TradingAgents]]

## Depends on
- [[C08 World state]]
- [[C14 Investment Constitution]]

---
[[MOC Build]]

> [!success] Built (2026-09-19)
> `src/financial_brain/committee/`. Analyst stances over a cited dossier, a bull/bear
> debate in which **uncited points are dropped**, and a deterministic chair that cannot
> draft a BUY against the [[C14 Investment Constitution]]. The chair now also emits typed
> invalidation checks, so what it drafts can be monitored.
