---
type: concept
tags:
  - concept
  - quant
  - gate
  - india
---

# India Implementability Gate

**New component, not in the original design.** The [[Research Brain]] will read a US
long-short momentum paper and produce a hypothesis that cannot be traded here.

Gate every hypothesis **before** backtesting:

- long-only feasible? (no shorting in Indian cash equities beyond intraday)
- universe liquid enough at target AUM?
- turnover survives STT + impact?
- F&O available if shorting is required? (~200 names, membership changes)
- circuit / ASM / GSM / T2T exposure?

FAIL -> record the reason, do not backtest.

See [[Indian microstructure]], [[C19 India Implementability Gate]].
