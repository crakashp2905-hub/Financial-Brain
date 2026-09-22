---
type: research
tags:
  - research
  - mistake
  - data-quality
date: 2026-09-22
---

# The insider feature contained no insider trades

A rejection that turned out to be about the data rather than the market.

[[Following disclosed insiders]] tested `insider_60d` and was rejected at **t = -0.35**.
Its pre-registration had named the expected failure mode - "this counts disclosures
without reading their direction, so a pledge release, a sale and a purchase all count the
same". That was true, and it was the wrong diagnosis.

## What the feature actually held

174,059 filings, counted:

| Share | What it was |
|---:|---|
| 60.5% | SAST Reg. 29 disclosures - direction present, in the attachment, unread |
| **30.6%** | **PIT Reg. 7(3) quarterly compliance certificates - no dealing reported** |
| 6.1% | PIT 1992 disclosures, **10,399 of 10,617 of them in 2015 alone** |
| 2.5% | codes of conduct - no dealing reported |
| 0.3% | trading plans |

A third of the signal reported no trade at all, and a further 6% was a regulatory regime
that ended in 2015, concentrated in a single year at the very start of the sample.

## The cause was one regex

    (r"reg\.?\s*7\s*\(|insider trading|\(pit\)", "INSIDER_DISCLOSURE", MEDIUM)

The rule was meant to catch PIT disclosures. The PIT regulations happen to number a
quarterly compliance certificate **7(3)**, so `reg 7(` swallowed 53,335 certificates
stating that a register had been maintained. A narrower rule, checked first, now types
those as `COMPLIANCE/low` - 57,726 rows moved, and **nothing else in the archive did**,
which is what a surgical rule change looks like.

## Why this matters more than the fix

`t = -0.35` was never evidence that insiders do not predict returns. It is what a feature
that does not contain the thing being tested returns. **A measurement validity failure,
not a finding** - and the most dangerous kind, because it produces a confident, properly
computed, fully firewalled number about nothing.

Three things follow.

**The cheap repair beat the expensive one.** The pre-registration's proposed next step was
to parse direction out of the filings - 95,751 attachment PDFs. That would have been an
expensive answer to a question the data could not yet support. Auditing what the feature
contained cost one query.

**Rules need sweeping, not just fixing.** Classification happens once, at ingest, so a
corrected rule reaches only future filings. Until `fb reclassify --apply` runs, the record
is split between filings typed under the old rule and filings typed under the new one.
That command now exists because this will not be the last rule to be wrong.

**Audit the population before believing the measurement.** Every other feature in
[[Factor Library]] deserves the same count - what is in it, by share, before its t-statistic
is taken seriously. This one looked fine from the outside for as long as nobody asked.

## What is being tested now

[[h8]] re-tests the same claim on the corrected population, pre-registered before the
result and **counted as a separate trial** - the population changed, not the threshold, so
it is a new test rather than a re-run. Its prior is deliberately weaker than h5's:
removing noise raises signal-to-noise but cannot manufacture a signal that was never
present, and the remaining 60.5% still counts an acquisition and a disposal identically.

Related: [[Following disclosed insiders]] · [[Alpha Validation Firewall]] ·
[[What follows a filing]] · [[Beating the median is not an edge]] · [[MOC Strategies]]
