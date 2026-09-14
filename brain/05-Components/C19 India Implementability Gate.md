---
type: component
phase: 3
status: not-started
resources: []
depends-on:
  - "[[C05 Indian cost model]]"
tags:
  - component
  - phase/3
---

# C19 India Implementability Gate

**Phase 3** · Reject untradeable hypotheses before backtesting

The Research Brain will read a US long-short momentum paper and produce a hypothesis
that cannot be traded here. Gate it **before** any backtest:

- long-only feasible?
- universe liquid enough at target AUM?
- turnover survives STT + impact?
- F&O available if shorting is required?
- circuit / ASM / GSM / T2T exposure?

FAIL -> record the reason, do not backtest. **New component; not in the original design.**

## Resources needed
- _none_

## Depends on
- [[C05 Indian cost model]]

---
[[MOC Build]]
