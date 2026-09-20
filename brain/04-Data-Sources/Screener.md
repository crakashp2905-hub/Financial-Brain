---
type: resource
tier: 3
verdict: WRAPPED
tags:
  - resource
  - data-source
---

# Screener

**Tier 3** · a compiler of company filings, not the filer

`fb fundamentals SYMBOL` reads the headline ratio block (market cap, P/E, book value,
ROCE, ROE, dividend yield) from `/company/<SYMBOL>/`, which its robots.txt allows;
`/company/source/quarter/*`, `/user/*` and query-sorted paths are refused.

Because it is Tier 3, its numbers are **compared, not trusted**: Screener's current price
against the Tier-1 close we computed ourselves. Agreement within 2% is recorded, and a
disagreement is stored `quality='disputed'`, never averaged away. Live check across the
watchlist: **29 agree, 0 disagree, 1 uncheckable**.

Its figures are also *restated*, which is why [[C03 PIT fundamentals store]] reads the
filings directly instead.

Related: [[Moneycontrol]] · [[Point-in-time fundamentals]]
