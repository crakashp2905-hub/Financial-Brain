# PE-0128 — stratified performance and fragmentation uncertainty

`stratify_outcomes()` requires a registered label set for volatility, market regime, liquidity, market-cap band and transaction-cost bucket. It reports each registered stratum, including empty buckets, against the full scheduled-session denominator. Missing dates withhold confidence bounds. Complete buckets receive simultaneous Newey-West bands with Bonferroni allocation across every registered bucket and both net-alpha and after-cost utility metrics. A positive bucket is not promoted to a global profitability claim.

`freeze_stratification_plan()` adds a strict cohort-bound path. It freezes the confirmation schedule, all dimension labels, HAC lag, familywise alpha and an aggregation-policy digest into a content-addressed plan tied to the `FrozenChampionPlan` digest. `stratify_frozen_outcomes()` verifies the cohort identity and plan hash before evaluating the observations. This makes caller-selected strata reproducible and binds them to the chosen confirmation schedule; it does not independently authenticate the cohort, aggregation policy or outcome rows.

**Status: PARTIAL.** Thirteen focused stratification tests, Ruff and mypy pass. The new content-addressed plan binds stratum labels and the aggregation-policy digest to the frozen champion confirmation plan and rejects altered cohort identity or late observation schedules. A durable authoritative cohort registry and independently verified aggregation policy and outcome inputs are still absent. No market cohort data is available here.

Input rows must represent one already-aggregated outcome per dimension, bucket and session; callers remain responsible for freezing how instrument outcomes and portfolio utility are allocated before observing results.

Evidence and source hashes are recorded in `pe-0128-stratified-outcomes-evidence.json`.
