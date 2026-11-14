# PE-0126 — registered fixed-look evaluation and immediate risk stop

`FixedLookPolicy` hashes the cohort's fixed session-count checkpoints, alpha allocation, drawdown harm floor, and freeze/first-observation times. Efficacy output is withheld between registered looks. The familywise alpha is Bonferroni-allocated across the frozen look count. A drawdown crossing the pre-registered harm floor stops at any observation, including between looks. Missing risk evidence does not authorize efficacy interpretation.

**Status: PARTIAL.** Seven focused tests and Ruff pass. The policy is not yet persisted in the authoritative cohort registry or wired to the live paper runner's persistent risk latch; the report returns a stop directive, but restart-safe enforcement remains outstanding. The checkpoint count is a monitoring schedule, not a sample-size sufficiency claim.

Synthetic tests validate behavior only. No live or prospective trading result is claimed.

Evidence and source hashes are recorded in `pe-0126-fixed-look-risk-stop-evidence.json`.
