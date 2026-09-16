---
type: component
phase: 0
status: done
resources:
  - "[[NSE bhavcopy]]"
  - "[[BSE bhavcopy]]"
  - "[[Company filings]]"
depends-on: []
tags:
  - component
  - phase/0
---

# C01 Security master

> [!success] Delivered in Phase 0
> See `docs/PHASE-0.md` and `src/financial_brain/`.

**Phase 0** · Canonical identity across NSE, BSE, Kite, Screener, filings

**ISIN is the primary key.** Symbols change, BSE codes differ, companies restructure.

Must carry: ISIN, exchange identifiers, symbol history, company and group identity;
corporate actions (mergers, splits, bonuses, dividends, symbol changes, suspensions,
delistings); adjusted and unadjusted OHLCV with explicit adjustment policy; instrument
universe membership at each historic point; entitlement/licence/freshness metadata.

Everything else depends on this. Budget several weeks and do it properly.

## Resources needed
- [[NSE bhavcopy]]
- [[BSE bhavcopy]]
- [[Company filings]]

## Depends on
- _none — can start immediately_

---
[[MOC Build]]
