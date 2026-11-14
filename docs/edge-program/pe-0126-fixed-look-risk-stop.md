# PE-0126 — registered fixed-look evaluation and immediate risk stop

`FixedLookPolicy` hashes the cohort's fixed session-count checkpoints, alpha allocation, drawdown harm floor, and freeze/first-observation times. Efficacy output is withheld between registered looks. The familywise alpha is Bonferroni-allocated across the frozen look count. A drawdown crossing the pre-registered harm floor stops at any observation, including between looks. Missing risk evidence does not authorize efficacy interpretation.

`ForwardPaperSessionService.run` accepts an optional `risk_stop_fn`. A validated `STOP_FOR_RISK_HARM` result trips the account's append-only risk latch before strategy evaluation; risk and decision callbacks are blocked, and the report is included in the durable runner risk verdict. Reopened accounts keep the latch and reject subsequent sessions until a human repair signoff clears it.

**Status: PARTIAL.** Seven fixed-look tests plus the paper-account bridge integration suite pass (28 tests combined); Ruff and mypy pass. The policy is not persisted in an authoritative cohort registry, so production sessions do not obtain this callback automatically. The checkpoint count is a monitoring schedule, not a sample-size sufficiency claim.

Synthetic tests validate behavior only. No live or prospective trading result is claimed.

Evidence and source hashes are recorded in `pe-0126-fixed-look-risk-stop-evidence.json`.
