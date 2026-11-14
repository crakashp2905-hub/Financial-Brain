# PE-0100 — Fill identity and correction safety

PaperAccount uses durable fill IDs to prevent duplicate economics. Exact replays are harmless, changed content under the same ID is rejected before state mutation, and the independent journal auditor rejects duplicate fill records.

This validates the paper ledger's idempotency contract. It does not claim that any broker has supplied or confirmed a fill.

Evidence and source hashes are in `pe-0100-provider-fill-idempotency-evidence.json`.
