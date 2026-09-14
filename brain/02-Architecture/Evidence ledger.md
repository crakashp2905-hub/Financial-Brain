---
type: concept
tags:
  - concept
  - contract
---

# Evidence ledger

Citation is not enough. Immutable ledger for every number, statement, extracted claim
and derived conclusion:

```
evidence_id -> source URI/document -> source tier -> content hash -> retrieval time
            -> event/publication time -> extraction/transformation -> confidence
            -> data-quality status -> world-state version -> decisions that used it
```

Claims never silently overwrite their source. A revised result, corrected filing or
reclassified entity creates a **new version** and retains the old.

This is what makes a recommendation reproducible and auditable. See [[C09 Evidence ledger]].
