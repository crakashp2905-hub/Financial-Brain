---
type: resource
tier: 1
verdict: INTEGRATED
tags:
  - resource
  - data-source
  - mutual-funds
---

# AMFI NAV feed

**Tier 1** · The industry body's own daily NAV file · free, public, no authentication

`portal.amfiindia.com/spages/NAVAll.txt` - ~14,375 schemes with ISINs, ingested daily by
`fb mfnav`. Rows are keyed by **the date each row carries**, not the date fetched: the
file mixes today's NAVs with stale ones, and a scheme that last reported in 2018 must not
be filed under today.

This is the first piece of the mutual-fund side of the mandate. What is still missing for
fund decisions: NAV **history** (AMFI publishes a historical report separately), scheme
category and benchmark, expense ratio, and rolling-return machinery.

Related: [[MFCentral]] · [[MOC Data]]
