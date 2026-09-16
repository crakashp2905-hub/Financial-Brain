---
type: component
phase: 0
status: done
resources: []
depends-on: []
tags:
  - component
  - phase/0
---

# C00 Raw data lake

**Phase 0** · Immutable landing zone for every byte we receive

> [!success] Delivered in Phase 0
> `src/financial_brain/lake/store.py`. See `docs/PHASE-0.md`.

Parsed tables are opinions; raw payloads are facts. Every payload lands here **before**
parsing and is never mutated, stored beside a `.meta.json` recording source URL,
retrieval time and SHA-256.

Why it comes before everything else:

- if a parser is wrong, the lake is the only thing that can rebuild history
- if a source silently restates a published file, both versions are kept - a source
  changing its bytes is a finding, not an update
- ingestion becomes **replayable**: `fb ingest --from-lake` rebuilds every curated table
  with the network switched off

Layout is plain filesystem so it needs no infrastructure today and maps 1:1 onto object
storage later - the path *is* the object key.

```
lake/<source>/<dataset>/<yyyy>/<mm>/<dd>/<filename>
lake/<source>/<dataset>/<yyyy>/<mm>/<dd>/<filename>.meta.json
```

Related: [[C02 Data provider abstraction]] · [[Evidence ledger]] · [[Four timestamps]]
