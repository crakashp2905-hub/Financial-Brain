---
type: concept
tags:
  - concept
  - quant
  - gate
---

# Alpha Validation Firewall

Mandatory before any strategy reaches paper trading.

```
Research Agent -> Hypothesis -> Factor Generator -> Backtest
                          |
        +---------------------------------+
        |        ALPHA VALIDATION         |
        |  IC / ICIR                      |
        |  Multiple-testing control       |
        |  Factor redundancy              |
        |  Deflated Sharpe                |
        |  Walk-forward                   |
        |  Out-of-sample                  |
        |  Transaction costs              |
        |  Capacity                       |
        |  Regime stability               |
        +----------------+----------------+
                         |
                  Strategy Judge -> REJECT / PROMOTE
```

> **The AI is not allowed to declare itself successful. The validation layer does that.**

Blueprint from [[EntroPy]]. More necessary in India — see
[[Multiple testing in a small universe]]. Built as [[C22 Alpha Validation Firewall]].
