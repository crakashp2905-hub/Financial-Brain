# PE-0125 — lower confidence bounds for alpha and portfolio utility

`confirmation_bounds()` evaluates matched daily net alpha and after-cost portfolio utility on the complete scheduled cohort. It reports a one-sided lower confidence bound using the frozen dependence policy's Newey-West standard error and alpha level. A positive point estimate is `NOT_CONFIRMED` when its lower bound is zero or negative. Missing scheduled dates remain visible and withhold the affected bound; zero-variance samples do not create a zero-width confirmation.

**Status: PARTIAL.** Five focused tests and Ruff pass. The report contract is not yet bound to the authoritative promotion gate or sample-size design, and no independently attested forward cohort is available. Callers must supply utility already net of transaction costs and the risk penalty under a frozen portfolio policy.

Synthetic data verifies software behavior only. It does not establish profitability or market-session evidence.

Evidence and source hashes are recorded in `pe-0125-confidence-bounds-evidence.json`.
