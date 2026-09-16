---
type: component
phase: 0
status: done
resources:
  - "[[Company filings]]"
  - "[[NSE BSE announcements]]"
  - "[[Screener]]"
depends-on:
  - "[[C01 Security master]]"
tags:
  - component
  - phase/0
---

# C03 PIT fundamentals store

> [!success] Delivered in Phase 0
> See `docs/PHASE-0.md` and `src/financial_brain/`.

**Phase 0** · The only dataset that compounds

> [!important] Start this month
> Snapshot every fundamental with an `observed_at` timestamp and **never overwrite**.
> In three years you own a dataset nobody can sell you.

Recovers partial PIT by keying fundamentals to the **announcement timestamp**, not the
quarter end. See [[Point-in-time fundamentals]], [[D2 Start the PIT store]].

## Resources needed
- [[Company filings]]
- [[NSE BSE announcements]]
- [[Screener]]

## Depends on
- [[C01 Security master]]

---
[[MOC Build]]
