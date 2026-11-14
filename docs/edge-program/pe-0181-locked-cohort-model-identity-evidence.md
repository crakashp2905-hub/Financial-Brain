# PE-0181 — Locked forward-cohort model identity

## Acceptance

Online model weight updates must be blocked while a prospective cohort is locked. Drift can raise an alarm, but cannot silently change the strategy identity used by that cohort.

## Implementation and verification

`evaluation.model_update_guard` stores a DuckDB append-only, hash-chained cohort journal. It binds a cohort to a model SHA-256 and protocol SHA-256, validates aware UTC event times, records drift without unlocking, and requires closure evidence to release the lock. `evaluation.timing.save` hashes the complete timing-model identity before persisting it: updates with a different identity fail while locked and identical writes become no-ops.

Verification: `tests/test_model_update_guard.py` and `tests/test_phase3_timing.py` passed as part of a 23-test focused run. The tests cover weight-update rejection during a locked cohort, non-unlocking drift alarms, identical-save no-op, evidence-backed close and permitted post-close update, and non-finite model rejection. Ruff and targeted mypy passed.

## Limits

The current integration guards the timing model save path. Other model stores must call the same guard before any online update. The journal is local DuckDB integrity evidence, not an independently attested receipt or tamper-proof external custody. A drift alarm should still trigger the separate safe-policy halt workflow.
