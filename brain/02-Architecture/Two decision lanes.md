---
type: concept
tags:
  - concept
  - principle
---

# Two decision lanes

"No BUY without a backtest" applies cleanly to repeatable quantitative strategies, not to
every single-company fundamental thesis. Keep both lanes explicit.

| | **Quantitative lane** | **Fundamental / discretionary lane** |
|---|---|---|
| Gate | versioned code; PIT universe and data; realistic costs/slippage/liquidity; train-test separation; walk-forward, OOS, robustness, capacity | source quality; falsifiable thesis; comparable historical cases; scenario/risk analysis; portfolio constraints; continuous invalidation monitoring |
| Promotion | paper trading before promotion | outcome postmortem |

Both share the same [[Evidence ledger]], [[Market World State]], [[Decision contract]],
risk gate and outcome store. **Neither may change live trading rules directly.**
