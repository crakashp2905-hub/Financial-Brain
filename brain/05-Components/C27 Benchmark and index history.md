---
type: component
phase: 0
status: done
resources: []
depends-on: []
tags: [component, phase/0]
---

# C27 Benchmark and index history

**Phase 0** · Daily close for every NSE index

> [!success] Delivered
> `NSEIndexCloseProvider` + `IndexCloseJob`

You cannot assess a strategy honestly without something to assess it against.
One archive file per day carries ~165 indices with open/high/low/close **plus P/E, P/B
and dividend yield** — the last three are a free valuation-context bonus that will matter
to [[Market Regime Brain]].

Constituent history remains unsourced: NSE does not publish
"NIFTY 500 membership as of date X" in clean form, so it must be accumulated forward or
bought. Levels are enough to benchmark returns; constituents are needed to reconstruct
index-relative universes.
