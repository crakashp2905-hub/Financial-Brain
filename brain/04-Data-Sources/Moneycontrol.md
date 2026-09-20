---
type: resource
tier: 3
verdict: WRAPPED - news only
tags:
  - resource
  - data-source
---

# Moneycontrol

**Tier 3, news only.** Its robots.txt disallows `/stocks/company_info/` and
`/financials/results/`, so fundamentals are off-limits here; [[Screener]] covers those.

Used on demand for articles a filing already links to - one at a time, with a crawl
delay, title and summary stored, never the body. Most of the value needed no fetching at
all: see [[News recovered from filings]].

`providers/robots.py` exists because Python's stdlib matcher ignores wildcards and read
`Disallow: /stocks/company_info/*` as **allowed**.

Related: [[Untrusted text boundary]] · [[News recovered from filings]]
