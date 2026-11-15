# PE-0131 — return distribution and top-contributor dependence

`contribution_report()` reports observed daily-return distribution summaries and contribution concentration by day, month and instrument. Per-instrument contributions must reconcile to the daily net return. It reports the best/worst date, month and instrument plus fixed-scheduled-denominator sensitivity after removing the best day, month or instrument. `ContributionRegistry` durably binds report inputs and their source digests to a frozen prospective cohort plan, recomputes the report on recovery, and rejects replacement.

**Status: PARTIAL.** Contribution-report and registry tests, Ruff, and targeted mypy pass. The report is bound to a durable frozen-cohort registry but is not yet wired into the prospective promotion report, and it still requires caller-supplied net attribution rows and source digests. Missing scheduled returns remain visible and the displayed distribution is explicitly observed-session-only while the cohort is incomplete. Caller digests do not authenticate source ledgers.

The metric is additive daily return contribution, not a compounded counterfactual portfolio replay. Removal sensitivities are diagnostics, not a claim that the strategy could have traded without the removed contributor.

Evidence and source hashes are recorded in `pe-0131-contributor-removal-report-evidence.json`.
