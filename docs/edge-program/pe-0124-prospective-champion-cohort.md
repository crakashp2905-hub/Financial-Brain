# PE-0124 — prospective, non-overlapping champion confirmation

`prospective_cohort.py` selects a champion using only development-session returns, with deterministic tie-breaking. It rejects overlapping or out-of-order development and confirmation sessions, requires the plan to be frozen before the first confirmation observation, and hashes the selection inputs and plan. Confirmation accepts outcomes only for scheduled sessions; a missing scheduled result leaves the summary mean unavailable instead of silently shrinking the denominator.

**Status: PARTIAL.** Focused contract tests pass. The frozen plan is an immutable value, but no durable authoritative cohort registry currently prevents an operator from creating a replacement plan after observing confirmation outcomes. The first-observation timestamp is caller-supplied and has no independent timestamp attestation. Until those controls are connected, this is a useful API boundary rather than an end-to-end prospective governance guarantee.

The tests use synthetic returns and prove software behavior only. They do not provide market-session evidence or establish strategy profitability.

Evidence and source hashes are recorded in `pe-0124-prospective-champion-cohort-evidence.json`.
