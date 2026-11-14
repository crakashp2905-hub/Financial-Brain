# PE-0132 — fail-closed edge confirmation gate

`evaluate_edge_confirmation()` returns `EDGE_CONFIRMED` only when the frozen protocol was registered before observations, the scheduled cohort meets the larger of 60 sessions or the power plan, every decision and outcome has a valid pre-entry receipt, robust lower bounds are positive, cost and fill stresses pass, fills/risk/integrity evidence passes, and all required falsification checks pass. It then requires an independent receipt over the release bundle before its deadline.

The release digest binds the exact protocol, trust-root material supplied to this evaluation, registration and session receipts, session timestamps and outcomes, cost/fill scenario economics, evidence digests, and gate results. Reusing a release receipt after changing an outcome or evidence changes the bundle digest and fails verification. Favorable or all-zero local metrics without valid independent receipts remain `NOT_CONFIRMED`.

**Status: PARTIAL.** Four focused tests and Ruff pass, including zero/favorable unreceipted inputs and a signed synthetic fixture that exercises digest tamper detection. The latter is software-only and is not evidence of a real provider or market cohort. No approved provider trust roots are pinned in deployment configuration, no authoritative preregistration registry is connected, and no prospective market data or promotion workflow is wired to this gate. The code therefore does not establish that any strategy has an edge; the independent provider, key custody, data rights, real forward cohort, and production integration remain blockers.

Evidence and source hashes are recorded in `pe-0132-edge-confirmation-gate-evidence.json`.
