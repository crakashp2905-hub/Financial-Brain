# PE-0125 — lower confidence bounds for alpha and portfolio utility

`confirmation_bounds()` evaluates matched daily net alpha and after-cost portfolio utility on the complete scheduled cohort. It reports a one-sided lower confidence bound using the frozen dependence policy's Newey-West standard error and alpha level. A positive point estimate is `NOT_CONFIRMED` when its lower bound is zero or negative. Missing scheduled dates remain visible and withhold the affected bound; zero-variance samples do not create a zero-width confirmation.

The bound allocates familywise alpha across both metrics and the frozen number of planned looks. `registered_confirmation_bounds()` adds a strict path that resolves the schedule from `ProspectiveCohortRegistry`, re-derives and checks `SampleSizePlan`, binds timestamps and alpha to the registered cohort and dependence policy, and verifies each session's net-alpha and after-cost-utility values against its durable outcome digest. It returns `SAMPLE_SIZE_NOT_MET` and withholds lower bounds when the scheduled cohort is shorter than the calculated requirement.

**Status: PARTIAL.** Nine focused confirmation tests pass; Ruff and mypy pass. The registered path binds the report to the durable cohort journal and the re-derived sample-size plan, and applies familywise alpha across metrics and planned looks. The report is not yet enforced by the authoritative promotion gate. After-cost utility's fee and risk-accounting lineage remains caller-supplied, timestamps and outcome digests are not independently attested, and no prospective market cohort is available.

Synthetic data verifies software behavior only. It does not establish profitability or market-session evidence.

Evidence and source hashes are recorded in `pe-0125-confidence-bounds-evidence.json`.
