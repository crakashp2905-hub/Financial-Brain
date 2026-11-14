# PE-0206 capability evidence audit

The capability audit manifest binds each curated capability to current SHA-256 values for its source files, executable test modules, maturity and matrix. A per-capability artifact hash and overall manifest hash bind those rows. CI runs the staleness check after the full test suite, so changed source/test content or maturity requires an explicit manifest update before a green merge.

The generator/checker passed two focused tests, Ruff and mypy locally. New CI is pending. Scope is the five entries in the curated capability matrix, not every platform capability, and file hashes do not establish prospective profitability or independent custody.
