# PE-0138 — information decay and turnover burden

`analyze_signal_decay()` reports information-coefficient time-to-peak and interpolated half-life by registered family, plus mean net alpha, mean turnover, and turnover-to-absolute-net-alpha at each declared horizon. `execute_signal_decay()` verifies the development dataset file digest before and after running every family/horizon/session cell, rejects holdout schedules, and requires matched decision identities across horizons. Near-zero alpha produces an explicitly undefined ratio. The report always returns `NO_CHANGE`; any horizon adoption must be a separately registered strategy version.

**Status: PARTIAL.** Six focused tests, Ruff, and targeted mypy pass. The runner delegates signal/portfolio calculations to a caller-provided evaluator and is not bound to the authoritative research dataset registry or prospective family evidence. There is no new-version registration receipt controlling horizon adoption. The included tests are synthetic software checks, not measured market half-life evidence.

Evidence and source hashes are recorded in `pe-0138-signal-decay-turnover-report-evidence.json`.
