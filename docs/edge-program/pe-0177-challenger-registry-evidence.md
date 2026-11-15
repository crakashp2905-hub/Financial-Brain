# PE-0177 — Hashable challenger registry and gated promotion

## Acceptance

Register trained models as immutable challengers and permit promotion only through separately authorized future evidence; training P&L alone must never promote a model.

## Implementation and verification

`evaluation.challenger_registry` appends canonical, hash-chained JSONL events for challenger registration and promotion. Optional owner-supplied HMAC-SHA256 keys sign records; without a key, records remain locally hash-verifiable but unsigned. Offline training can append a `CHALLENGER_ONLY` entry and records its registry hash in model metadata; it has no promotion path. Promotion is a separate operation requiring a preauthorized prospective cohort, VERIFIED status, independent-receipt coverage, at least 60 verified sessions, positive net-alpha and matched-control lower bounds, cost stress, multiplicity adjustment, a nonempty candidate count, and a scoped human `STRATEGY_ENROLLMENT` approval. Duplicate model registrations and promotions are refused; tampered chains or invalid signatures fail closed.

Verification: challenger-registry and offline-training focused suite — 16 passed. Ruff and targeted mypy passed.

## Limits

The HMAC key must be protected and supplied by the owner; this does not constitute independent custody. Promotion evidence is an interface contract and must come from a genuine, frozen, independently verified prospective cohort. No model is promoted and no live profitability is asserted.
