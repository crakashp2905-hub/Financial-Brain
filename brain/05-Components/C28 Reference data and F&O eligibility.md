---
type: component
phase: 0
status: done
resources: []
depends-on:
  - "[[C01 Security master]]"
tags: [component, phase/0]
---

# C28 Reference data and F&O eligibility

**Phase 0** · Listing dates, face value, market lot, and the short-ability flag

> [!success] Delivered
> `NSEEquityListProvider`, `NSEFnoLotsProvider`, `EquityListJob`, `FnoEligibilityJob`

**EQUITY_L.csv** gives 2,577 NSE securities with **listing dates** — which bound how far
back a name could possibly have belonged to any universe, a direct input to
[[Survivorship bias]] control.

**fo_mktlots.csv** gives the F&O underlyings, and its real job is the `FNO_ELIGIBLE`
flag. Indian cash equities cannot be shorted beyond intraday and SLB is thin, so
"is there a future on this?" is the binary short-ability test that
[[India Implementability Gate]] needs.

## Not sourced
ASM / GSM / T2T surveillance flags have no archive-host endpoint and sit behind the
browser-gated API. `security_flags` therefore carries F&O eligibility only. These affect
margins and position limits rather than the binary can-we-trade-it question, so the most
important gate input is covered.
