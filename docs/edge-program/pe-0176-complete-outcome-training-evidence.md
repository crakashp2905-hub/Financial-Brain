# PE-0176 — Quarantine open and unsettled training outcomes

## Acceptance

Train only on properly matured, complete outcomes; quarantine open trades and pending settlements so future or unknown exits cannot become labels.

## Implementation and verification

The offline training schema now requires an explicit `outcome_status`. Only `MATURED` and `SETTLED` rows can enter training or temporal validation folds. `OPEN`, `PENDING_SETTLEMENT`, missing, unknown, and otherwise incomplete statuses are quarantined. The v3 training manifest binds this completeness contract and records open and pending-settlement counts; model loading rejects older or mismatched completeness contracts. Dataset identity hashes include the outcome status.

Verification: `tests/test_phase5_learned_model.py` — 12 passed, including matured-only eligibility, explicit open-trade and pending-settlement quarantine, future timestamps, and manifest lineage. Ruff and targeted mypy passed.

## Limits

The upstream outcome producer must classify statuses truthfully and provide correct maturity/revision times. Synthetic tests validate fail-closed behavior, not the correctness of real settlement feeds or predictive quality.
