# PE-0139 — stop/target development sensitivity

`evaluate_stop_target_sensitivity()` evaluates every frozen stop/target fraction pair over DEVELOPMENT OHLC paths. `run_stop_target_sensitivity_from_file()` loads a JSON trade-path dataset only when its exact file digest matches the frozen plan, then rechecks the file digest after evaluation. The shared fill resolver assigns an intrabar bar touching both levels to the adverse stop first, and declared round-trip costs enter every trade outcome. The report retains every grid-cell/trade outcome, negative counts, same-bar adverse-stop counts, and the protected final-window dates. Inputs that touch the final window or violate OHLC constraints are rejected.

**Status: PARTIAL.** Six focused sensitivity tests, Ruff, and targeted mypy pass. The exact dataset bytes are checked against the frozen plan, but the data digest remains caller-declared and the file runner supports JSON trade paths rather than a verified central dataset registry. Costs are supplied per trade and daily OHLC cannot establish the true intrabar sequence; ambiguity is deliberately penalized. No test output establishes that any exit policy is profitable.

Evidence and source hashes are recorded in `pe-0139-stop-target-development-sensitivity-evidence.json`.
