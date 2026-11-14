# PE-0206 capability evidence audit

The capability audit manifest binds each curated capability to current SHA-256 values for its source files, executable test modules, maturity and matrix. A per-capability artifact hash and overall manifest hash bind those rows. CI runs the staleness check after the full test suite, so changed source/test content or maturity requires an explicit manifest update before a green merge.

The generator/checker passed two focused tests, Ruff and mypy locally. The full cross-platform [CI run #598](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43601378017) passed the post-suite manifest freshness check on both Ubuntu and Windows at commit `2c61abe6cbc50e2efa864fb8c883a734431894ef`. Scope remains the five entries in the curated capability matrix, not every platform capability, and file hashes do not establish prospective profitability or independent custody.
