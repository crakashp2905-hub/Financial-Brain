---
type: concept
tags:
  - concept
  - execution
  - principle
---

# Phased autonomy

1. [[Kite Connect]] **read-only** -> portfolio + market data -> agent -> recommendation
2. Agent -> signal -> paper trade -> evaluate
3. Agent -> trade proposal -> **human approves** -> Kite executes
4. agent -> risk engine -> automated execution - **built 2026-10-13, locked** ([[ADR-0005 broker path behind stages]]):
   stage SMALL_AUTO needs a CONFIRMED strategy and a human promotion; none qualifies yet

Kite requires a static IP for order placement and caps order rate; SEBI's retail-algo
framework governs automated flow. See [[Regulatory gates]].
