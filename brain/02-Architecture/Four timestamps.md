---
type: concept
tags:
  - concept
  - contract
---

# Four timestamps

Every fact carries four times:

- **event time** — when it occurred
- **publication time** — when it became public
- **ingestion time** — when Financial-Brain saw it
- **effective / as-of time** — the latest information that could validly have informed a decision

Indispensable against [[Point-in-time fundamentals]] look-ahead and [[Survivorship bias]].
In India this is not optional — no vendor supplies PIT fundamentals affordably, so we
generate our own by never overwriting an observation. See [[C03 PIT fundamentals store]].
