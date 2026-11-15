# PE-0124 — prospective, non-overlapping champion confirmation

`prospective_cohort.py` selects a champion using only development-session returns, with deterministic tie-breaking. It rejects overlapping or out-of-order development and confirmation sessions, requires the plan to be frozen before the first confirmation observation, and hashes the selection inputs and plan. Confirmation accepts outcomes only for scheduled sessions; a missing scheduled result leaves the summary mean unavailable instead of silently shrinking the denominator.

`ProspectiveCohortRegistry` adds a durable hash-chained journal for immutable cohort plans and per-session outcome digests. It rejects plan replacement, observations outside the frozen schedule or before the declared cohort start, and conflicting updates for an already recorded session. Identical registrations and receipts are idempotent across restarts. This makes the application journal authoritative for its own record; the caller-supplied timestamps and outcome digests are not independently authenticated by the registry.

**Status: PARTIAL.** Fifteen focused cohort and registry tests, Ruff and mypy pass. The append-only registry prevents plan replacement and conflicting per-session outcome updates across restart. The first-observation timestamp and outcome digests remain caller-supplied without independent attestation or automatic reconciliation to receipt-verified paper-account outcomes. No prospective market cohort has elapsed.

The tests use synthetic returns and prove software behavior only. They do not provide market-session evidence or establish strategy profitability.

Evidence and source hashes are recorded in `pe-0124-prospective-champion-cohort-evidence.json`.
