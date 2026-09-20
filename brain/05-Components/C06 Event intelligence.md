---
type: component
phase: 1
status: done
resources:
  - "[[NSE BSE announcements]]"
  - "[[Indian finance news RSS]]"
  - "[[RBI]]"
depends-on:
  - "[[C01 Security master]]"
tags:
  - component
  - phase/1
---

# C06 Event intelligence

**Phase 1** · Company, macro and market events

Company: results, calls, orders, promoter activity, pledging, buybacks, ratings.
Macro: RBI, inflation, GDP, PMI, rates, crude, USD/INR, policy.
Market: FII/DII, rebalancing, rotation, volatility, breadth, derivatives positioning.

## Resources needed
- [[NSE BSE announcements]]
- [[Indian finance news RSS]]
- [[RBI]]

## Depends on
- [[C01 Security master]]

---
[[MOC Build]]

> [!success] Built (2026-09)
> Deterministic rules classify 3.08M announcements at **macro-F1 0.99** on a held-out
> labelled set - no model involved. Shareholder tone is a separate, calibrated question
> handled by the [[Model router]], and [[News recovered from filings]] supplies the
> headline the boilerplate hides.
