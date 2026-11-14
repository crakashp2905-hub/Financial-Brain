# PE-0134 — evidence-linked loss categories

`classify_loss()` supports false signal, unfavorable regime, timing, execution cost, size, stale data, invalid universe, and infrastructure categories. A losing trade without evidence IDs is explicitly `UNKNOWN`; multiple evidence-backed categories and their IDs are preserved. Non-losses are labeled `NOT_A_LOSS`. The DuckDB store is insert-once and idempotent for identical records; a changed classification for an existing trade ID raises a conflict instead of overwriting history.

**Status: PARTIAL.** Six focused tests and Ruff pass. The classifier records caller-supplied evidence identifiers but does not resolve them against an authoritative evidence registry or establish causality. It is not yet populated by the loss-review workflow or attached to the canonical paper trade ledger. Until that integration exists, classifications are useful review records, not verified root causes.

Evidence and source hashes are recorded in `pe-0134-evidence-linked-loss-categories-evidence.json`.
