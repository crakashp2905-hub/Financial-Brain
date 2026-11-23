# PR #40 trial-governance reconciliation

The forward-edge trial controls on the current branch preserve the required governance boundary:

- **Frozen quarter budget:** ADR-0004 starts from eight trials and records owner decisions amending Q4-2026 to 9, 10, 11, then 13. `trial-program.v1.json` binds Q4-2026 to ADR-0004 and its explicit budget of 13. The protected ledger records 13 `kind=trial` rows; the preservation audit verifies their outcomes and source hashes. All 13 remain failures.
- **Exhaustion is enforced:** the Q4 budget is 13/13. `assert_new_protected_trial_allowed` refuses another trial once spent. A capacity increase requires a recorded larger budget, amendment ADR and separate exact-scope `TRIAL_BUDGET_AMENDMENT` from the configured human owner.
- **Protocol and trial authorization are distinct:** Q1-2027 is `NOT_AUTHORIZED`, has no budget or protocol ADR, and cannot accept a generic trial approval. Activation requires an owner-approved Q1 protocol amendment and explicit positive budget; each trial also needs its own valid human `PROTECTED_TRIAL` approval.
- **Experiment identity and cohorts are frozen:** a protected trial requires an `ExperimentCharter`, non-empty `CohortSchedule` bound to that charter, matching ID and prospective start date, a cohort covering the proposed date, and an approval scope hash over both charter and schedule.
- **Historical records are immutable:** protected-file pins and the cross-platform preservation audit detect edits; this reconciliation adds no trials, changes no verdict, and does not backfill missing historical read windows.

## Verification evidence

- `python -m pytest --basetemp .pytest-tmp-pa0103 tests/test_forward_edge_f00.py tests/test_forward_edge_integrity_gates.py tests/test_forward_edge_tracker.py -q`: 66 passed.
- `python scripts/validate_trial_program.py`: passed; Q1-2027 remains unauthorized.
- `python scripts/trial_preservation_audit.py --check`: verifies pinned historical artifacts and ledger evidence.

This documents preserved controls. It does not authorize new research capacity or infer a profitable edge.
