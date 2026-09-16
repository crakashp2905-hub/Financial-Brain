---
type: component
phase: 0
status: done
resources:
  - "[[NSE bhavcopy]]"
  - "[[BSE bhavcopy]]"
depends-on:
  - "[[C01 Security master]]"
tags:
  - component
  - phase/0
---

# C04 Universe snapshots

> [!success] Delivered in Phase 0
> See `docs/PHASE-0.md` and `src/financial_brain/`.

**Phase 0** · Point-in-time investable universe

Daily snapshot of what was actually tradeable: listings, delistings, suspensions, T2T,
ASM/GSM. Without this, momentum and quality backtests overstate returns materially.
See [[Survivorship bias]].

## Resources needed
- [[NSE bhavcopy]]
- [[BSE bhavcopy]]

## Depends on
- [[C01 Security master]]

---
[[MOC Build]]
