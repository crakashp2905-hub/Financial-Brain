---
type: component
phase: 4
status: not-started
resources:
  - "[[Kite Connect]]"
  - "[[NautilusTrader]]"
depends-on:
  - "[[C17 Decision record and lifecycle]]"
tags:
  - component
  - phase/4
---

# C18 Execution gateway

**Phase 4** · Proposal -> risk -> human -> Kite

The harness, not the model, enforces validation:
`Proposed Order -> Risk -> Exposure -> Position -> Market-state -> Compliance ->
Human approval -> Broker`.

Verify SEBI/broker requirements before writing the order path. See [[Regulatory gates]].

## Resources needed
- [[Kite Connect]]
- [[NautilusTrader]]

## Depends on
- [[C17 Decision record and lifecycle]]

---
[[MOC Build]]
