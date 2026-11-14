# PE-0172 — Purged and embargoed temporal training folds

## Acceptance

Training and validation splits must prevent overlapping outcome horizons and nearby events from leaking across fold boundaries. Feature availability and revisions must be no later than the row decision. Training labels and their revisions must be known before the validation decision window. The final reserved validation block must not be used to fit the saved challenger.

## Implementation and verification

`financial_brain.evaluation.offline_training.purged_embargo_splits` builds expanding chronological folds. It excludes the configured embargo sessions immediately before each validation block, purges training outcomes whose maturity or latest revision reaches the validation start, and records deterministic membership hashes, row counts, fold boundaries, and maximum training label timestamps. The offline training job requires feature availability/revision and label maturity/revision timestamps. It fails closed if a feature or its revision postdates its decision, quarantines labels that were not matured and revised by the training cutoff, and fits the final artifact only on the last fold's purged training rows; that fold's validation rows are reserved. The v2 model manifest records the split contract and fold evidence, and the loader checks that training labels/revisions precede validation and that fitted rows equal the final fold's train membership.

Verification: `tests/test_phase5_learned_model.py` — 10 passed. Ruff and targeted mypy passed. Source inventory check passed (618 Python files); strategy-variant inventory check passed (253 linked candidates). Tests use synthetic timestamps and outcomes only.

## Limits

The session embargo is configured in trading sessions, while label intervals use the supplied maturity timestamps; callers must supply correct session calendars and timestamp provenance. The fold membership hashes establish reproducibility, not authenticity. No real market-data validity, predictive skill, profitability, or forward validation is claimed. CI for this package is pending.
