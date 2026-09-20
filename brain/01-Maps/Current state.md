---
type: map
tags:
  - map
  - state
updated: 2026-09-20
---

# Current state

What exists and runs today, with the numbers as measured rather than hoped.

## The daily cycle
`fb daily` runs: prices -> index levels -> corporate actions -> announcements -> regime
-> features + promoter graph -> [[AMFI NAV feed|mutual-fund NAVs]] -> as-reported
financials -> news references -> linked articles -> tone -> invalidation monitor ->
paper marks -> brief. Each step reports separately, so one failure never costs the brief.

## Data held
| Dataset | Extent |
|---|---|
| NSE + BSE prices | 2015-2026, 2,903 sessions, 16M rows, 17,060 ISINs |
| Announcements | 3.08M, classified by rules at macro-F1 0.99 |
| Corporate actions | 895, every price gap triaged |
| Filing attachments | 2.78M URLs held; documents read on demand |
| News references | 19,732 filings, 7,146 with a recovered headline |
| Mutual-fund NAVs | ~14,375 schemes daily, with ISINs |
| As-reported financials | point in time, append-only ([[C03 PIT fundamentals store]]) |

## What the models are allowed to say
- [[Model router]]: sentiment routes verified out-of-sample at the strict 10% bar -
  filings `fin-r1 -> phi4` (88.5% held out), headlines `fin-r1 -> llama3.1:8b -> gemma2`
  (87.4%, **91% of adverse headlines**, declines 15%).
- A route measured under a different prompt is treated as **no route at all**.
- A text type with no verified route is not classified at all.

## What it refuses
- Claims without a citation to the bytes they came from ([[Evidence ledger]])
- A number the filing's document does not name, or that contradicts the event type
- A results statement whose own arithmetic does not reconcile
- Text that tries to steer a model ([[Untrusted text boundary]])
- A BUY that breaches the [[C14 Investment Constitution]]

## What it has not shown
**Nothing about skill.** See [[The unanswered question]] and [[Own-record scorecard]].

Related: [[MOC Build]] · [[Home]]
