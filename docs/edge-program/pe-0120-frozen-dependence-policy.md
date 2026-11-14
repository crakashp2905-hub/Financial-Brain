# PE-0120 — Frozen dependence policy

A hashable policy now freezes Newey–West lags, stationary-bootstrap mean block, resample count, seed, alpha, and claimed freeze/first-observation times. Construction refuses non-timezone-aware dates and a policy frozen after observation. The resulting summary includes effective sample size and both HAC and bootstrap uncertainty.

**Status: PARTIAL.** The policy object enforces an ordering contract, but the timestamps are supplied by the caller and no production cohort registry independently attests them. No forward sample has elapsed to validate the registered lag/block choices.

Evidence and source hashes are in `pe-0120-frozen-dependence-policy-evidence.json`.
