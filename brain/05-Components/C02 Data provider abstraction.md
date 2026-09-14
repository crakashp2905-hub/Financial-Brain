---
type: component
phase: 0
status: not-started
resources:
  - "[[OpenBB]]"
depends-on:
  - "[[C01 Security master]]"
tags:
  - component
  - phase/0
---

# C02 Data provider abstraction

**Phase 0** · One interface, many providers

`financial_brain.data.Provider`. [[OpenBB]] is **one implementation behind it**, never
the core — AGPL. Lets us swap providers as licensing and budget change.

## Resources needed
- [[OpenBB]]

## Depends on
- [[C01 Security master]]

---
[[MOC Build]]
