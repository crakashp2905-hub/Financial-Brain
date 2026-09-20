---
type: component
phase: 2
status: done
depends-on:
  - "[[C21 India evaluation benchmark]]"
tags:
  - component
  - phase/2
  - models
---

# Model router

**Phase 2** · The cheapest model that is *measured* good enough - or nothing

> [!success] Built
> `src/financial_brain/llm/router.py`, `evaluation/models.py`. See ADR-0002.

Models are chosen by measurement on our own labelled Indian filings, never by
reputation. Each candidate answers through one typed interface
([[Typed decisions]]) and earns a **per-label acceptance threshold**: the confidence
above which *that label* from *that model* was right at least 90% of the time.

What measurement overturned:
- **FinBERT** scored macro-F1 0.45 on Indian filings and called 17 of 19 adverse filings
  neutral. **FinSenti-1B** scored 0.05. Both carry "financial" billing; both rejected.
- **Fin-R1**, assumed reasoning-only, answers typed decisions in ~2.5s and is the only
  local model earning a threshold for all three labels. It leads the deployed route.
- Cheap cascades looked best in-sample and **failed out-of-sample** (23.6% wrong when
  accepted). Route selection now *requires* verification on a held-out set.

Two refusals are built in: a task with no verified route is declined, and a route
calibrated under a different prompt is [[Calibration belongs to a prompt|no route at all]].

Related: [[C21 India evaluation benchmark]] · [[Own-record scorecard]] · [[Base rates]]
