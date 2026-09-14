---
type: component
phase: 1
status: not-started
resources: []
depends-on:
  - "[[C08 World state]]"
tags:
  - component
  - phase/1
---

# C09 Evidence ledger

**Phase 1** · Immutable provenance for every claim

`evidence_id -> source URI -> tier -> content hash -> retrieval time -> event/publication
time -> extraction -> confidence -> data-quality -> world-state version -> decisions used`.

Claims never silently overwrite their source. See [[Evidence ledger]].

## Resources needed
- _none_

## Depends on
- [[C08 World state]]

---
[[MOC Build]]
