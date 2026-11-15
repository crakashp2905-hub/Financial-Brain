# PE-0126 — registered fixed-look evaluation and immediate risk stop

`FixedLookPolicy` hashes the cohort's fixed session-count checkpoints, alpha allocation, drawdown harm floor, and freeze/first-observation times. Efficacy output is withheld between registered looks. The familywise alpha is Bonferroni-allocated across the frozen look count. A drawdown crossing the pre-registered harm floor stops at any observation, including between looks. Missing risk evidence does not authorize efficacy interpretation.

`FixedLookRegistry` persists policies and power-based `SampleSizePlan`s in a hash-chained journal and binds the bundle digest to a registered `ExperimentCharter` revision's `evaluation_identity.risk_policy_sha256`. Registration re-derives and validates the sample plan and requires its alpha, look count, and timestamps to match the fixed-look policy; the final look must reach the calculated required-session count. Recovery checks the archived charter revision, and resolving a policy for a new session fails after the charter advances. Registered efficacy looks before the required sample return `SAMPLE_SIZE_NOT_MET` and withhold efficacy. `ForwardPaperSessionService.run` resolves and evaluates this registered package from a supplied observation callback. A harm breach trips the account's append-only risk latch before strategy evaluation; risk and decision callbacks are blocked, and the report is included in the durable runner risk verdict. Reopened accounts keep the latch and reject subsequent sessions until a human repair signoff clears it.

**Status: PARTIAL.** The full Windows suite passes (4,164 passed, 1 skipped). The focused fixed-look, registry, sample-size, charter and paper-bridge suites pass (104 tests); Ruff and mypy pass for changed modules. Production session configuration does not yet require this registry or bind the observation callback to receipt-derived cohort metrics. The package does not yet gate the authoritative promotion decision on prospective outcomes.

Synthetic tests validate behavior only. No live or prospective trading result is claimed.

Evidence and source hashes are recorded in `pe-0126-fixed-look-risk-stop-evidence.json`.
