# PE-0126 — registered fixed-look evaluation and immediate risk stop

`FixedLookPolicy` hashes the cohort's fixed session-count checkpoints, alpha allocation, drawdown harm floor, and freeze/first-observation times. Efficacy output is withheld between registered looks. The familywise alpha is Bonferroni-allocated across the frozen look count. A drawdown crossing the pre-registered harm floor stops at any observation, including between looks. Missing risk evidence does not authorize efficacy interpretation.

`FixedLookRegistry` persists policies in a hash-chained journal and binds each policy digest to a registered `ExperimentCharter` revision's `evaluation_identity.risk_policy_sha256`. Recovery checks the archived charter revision; resolving a policy for a new session fails after the charter advances. `ForwardPaperSessionService.run` can resolve and evaluate this registered policy from a supplied observation callback. A harm breach trips the account's append-only risk latch before strategy evaluation; risk and decision callbacks are blocked, and the report is included in the durable runner risk verdict. Reopened accounts keep the latch and reject subsequent sessions until a human repair signoff clears it.

**Status: PARTIAL.** Fixed-look, registry, charter-recovery, and paper-account bridge suites pass (89 tests combined); Ruff and mypy pass. The production session configuration does not yet require the registry or bind the observation callback to receipt-derived cohort metrics. The policy also is not automatically joined to the power-based sample-size plan or prospective outcomes. The checkpoint count is a monitoring schedule, not a sample-size sufficiency claim.

Synthetic tests validate behavior only. No live or prospective trading result is claimed.

Evidence and source hashes are recorded in `pe-0126-fixed-look-risk-stop-evidence.json`.
