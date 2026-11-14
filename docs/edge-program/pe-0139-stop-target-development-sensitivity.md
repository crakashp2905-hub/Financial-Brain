# PE-0139 — stop/target development sensitivity

`evaluate_stop_target_sensitivity()` evaluates every frozen stop/target fraction pair over caller-supplied DEVELOPMENT OHLC paths. It reuses the shared fill resolver, which assigns an intrabar bar touching both levels to the adverse stop first, and includes declared round-trip costs in each trade outcome. The report retains every grid-cell/trade outcome, negative counts, same-bar adverse-stop counts, and the protected final-window dates. Inputs that touch the final window or violate OHLC constraints are rejected.

**Status: PARTIAL.** Twenty-four focused sensitivity, fill-kernel, and ORB tests pass; Ruff passes. The package exercises a complete supplied grid but does not yet run against a verified development dataset or lock data provenance in a central registry. Costs are supplied per trade and the daily OHLC path cannot establish the true intrabar sequence; ambiguity is deliberately penalized. No test output establishes that any exit policy is profitable.

Evidence and source hashes are recorded in `pe-0139-stop-target-development-sensitivity-evidence.json`.
