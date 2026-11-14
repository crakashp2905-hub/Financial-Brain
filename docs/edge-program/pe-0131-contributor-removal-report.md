# PE-0131 — return distribution and top-contributor dependence

`contribution_report()` reports observed daily-return distribution summaries and contribution concentration by day, month and instrument. Per-instrument contributions must reconcile to the daily net return. It reports the best/worst date, month and instrument plus fixed-scheduled-denominator sensitivity after removing the best day, month or instrument. Report identity is hashed with the frozen protocol, schedule and timestamps so these diagnostics cannot silently rewrite the declared protocol.

**Status: PARTIAL.** Eight focused tests and Ruff pass. The report is not yet wired to the prospective promotion report or an immutable evidence registry, and it requires caller-supplied net attribution rows. Missing scheduled returns remain visible and the displayed distribution is explicitly observed-session-only while the cohort is incomplete.

The metric is additive daily return contribution, not a compounded counterfactual portfolio replay. Removal sensitivities are diagnostics, not a claim that the strategy could have traded without the removed contributor.

Evidence and source hashes are recorded in `pe-0131-contributor-removal-report-evidence.json`.
