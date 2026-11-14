# PE-0180 — Cumulative search budgets and candidate-count correction

## Acceptance

Limit strategy/hyperparameter search budgets and include every attempted candidate in the multiplicity family, including failures and interrupted work.

## Implementation and verification

`ResearchVariantJournal` now assigns runs to an explicit frozen search campaign (defaulting to the mechanism family), applies a cumulative candidate budget across runs, and refuses budget or familywise-alpha changes. Each run registers all variants before evaluation. The campaign denominator includes every registered candidate regardless of EVALUATED, FAILED, SKIPPED, or ABORTED outcome. `campaign_state` reports the full candidate count, remaining budget, familywise alpha, and Bonferroni per-candidate alpha. Existing per-run caps remain in force.

Verification: `tests/test_research_library.py` — 6 passed, including repeated runs, failed/aborted candidates, cumulative budget exhaustion, and frozen multiplicity correction. Ruff and targeted mypy passed.

## Limits

The journal can count only attempts submitted to the governed campaign API. Research performed outside it cannot be detected automatically and must be disclosed or treated as an unregistered search family. This is research governance, not evidence that any candidate has an edge.
