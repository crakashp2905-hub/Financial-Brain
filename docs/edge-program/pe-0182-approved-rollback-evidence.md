# PE-0182 — Approval-scoped paper rollback

## Acceptance

After forecast-drift or harm alarms, the system can roll back to the immediately preceding approved version. Version and capital changes are immutable events and require correctly scoped human authorization.

## Implementation and verification

`forward_edge.approved_rollback` adds an append-only DuckDB hash-chained journal for approved versions, drift/harm alarms, and rollback events. A rollback is available only when an alarm refers to the active version and the target is exactly the preceding approved version. The human `ROLLBACK` approval binds family, source and target versions, current and requested paper capital, and all alarm-evidence hashes. A capital change requires a separate human `CAPITAL` approval scoped to the exact rollback and amounts. Replay verifies event order, version transitions, alarm coverage, and hash integrity. This module changes recorded paper-policy state only; it has no broker execution path.

Verification: `tests/test_approved_rollback.py` passed within a 23-test focused run. It covers valid rollback, absent alarm, wrong approval kind/stale scope, separately approved capital reduction, and journal tampering. Ruff and targeted mypy passed.

## Limits

The journal requires the caller to provide genuine, independently collected alarm evidence and human approvals. It does not itself detect forecast drift/harm or activate the safe-policy halt. It is a paper strategy governance primitive, not authorization for live capital or order routing.
