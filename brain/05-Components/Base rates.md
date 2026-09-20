---
type: component
phase: 2
status: done
depends-on:
  - "[[C06 Event intelligence]]"
tags:
  - component
  - phase/2
  - depth
---

# Base rates

**Phase 2** · How unusual is this, here and against the market

> [!success] Built
> `src/financial_brain/features/baserates.py`.

A fact without a base rate is not analysis. "The auditor resigned" reads the same whether
it is the first in a decade or the third in three years. Every red-flag filing in the
brief now carries both comparisons, computed as of the session so nothing leaks from
later filings:

> insolvency - 3 times here in three years, last on 2026-08-29; 1.4% of 4,962 companies
> filed one in the last year

**Rarity of the event type is not enough.** NRB Bearing files 47 shareholding disclosures
in three years and so do its peers; flagging that forever would be noise. A company is
called a *pattern* only when it files more than the 90th percentile of other filers of
the same type over the same window - 47 against a peer p90 of 19 is a pattern, 16 against
19 is context.

Known limit: peers are **all listed companies**, because the reference data has no
reliable sector mapping yet. The claim says so.

Related: [[C24 Knowledge Graph]] · [[Current state]]
