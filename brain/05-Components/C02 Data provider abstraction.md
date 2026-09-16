---
type: component
phase: 0
status: done
resources:
  - "[[OpenBB]]"
depends-on:
  - "[[C01 Security master]]"
tags:
  - component
  - phase/0
---

# C02 Data provider abstraction

> [!success] Delivered in Phase 0
> See `docs/PHASE-0.md` and `src/financial_brain/`.

**Phase 0** · One interface, many providers

`financial_brain.data.Provider`. [[OpenBB]] is **one implementation behind it**, never
the core — AGPL. Lets us swap providers as licensing and budget change.

## Resources needed
- [[OpenBB]]

## Depends on
- [[C01 Security master]]

---
[[MOC Build]]
