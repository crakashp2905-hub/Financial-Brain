# PE-0134 — evidence-linked loss categories

`classify_loss()` supports false signal, unfavorable regime, timing, execution cost, size, stale data, invalid universe, and infrastructure categories. A losing trade without evidence IDs is explicitly `UNKNOWN`; multiple evidence-backed categories and their IDs are preserved. Non-losses are labeled `NOT_A_LOSS`. `LossEvidenceRegistry` durably maps an evidence ID to a category and artifact digest. The verified classification path resolves each ID and carries the artifact digest into the immutable DuckDB record; unregistered IDs and category mismatches fail closed. The DuckDB store is insert-once and idempotent for identical records; a changed classification for an existing trade ID raises a conflict instead of overwriting history.

**Status: PARTIAL.** Focused registry/classification tests, Ruff, and targeted mypy pass. The registry is local and append-only, but it does not authenticate source artifacts or establish causality. The classifier is not yet populated by the loss-review workflow or attached to the canonical paper trade ledger. Until that integration exists, classifications are evidence-linked review records, not verified root causes.

Evidence and source hashes are recorded in `pe-0134-evidence-linked-loss-categories-evidence.json`.
