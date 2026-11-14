# PE-0138 — information decay and turnover burden

`analyze_signal_decay()` reports information-coefficient time-to-peak and interpolated half-life by registered family, plus mean net alpha, mean turnover, and turnover-to-absolute-net-alpha at each declared horizon. It requires matched development sessions/decision identities across every horizon, keeps the complete observation log, and rejects non-development or protected-final-window inputs. Near-zero alpha produces an explicitly undefined ratio. The report always returns `NO_CHANGE`; any horizon adoption must be a separately registered strategy version.

**Status: PARTIAL.** Nine focused tests pass and Ruff passes. The analysis consumes caller-provided development rows and declared data digest. It is not bound to the authoritative research dataset registry or prospective family evidence, and there is no new-version registration receipt controlling horizon adoption. The included tests are synthetic software checks, not measured market half-life evidence.

Evidence and source hashes are recorded in `pe-0138-signal-decay-turnover-report-evidence.json`.
