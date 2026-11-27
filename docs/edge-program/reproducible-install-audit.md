# Reproducible install audit

The install contract pins Python 3.13.5, the `requirements-lock.txt` digest,
the timezone and deterministic seed. The CI reproducibility matrix installs
that lock on Ubuntu 24.04 and Windows 2022, checks the environment and runs the
deterministic fixture acceptance test.

`scripts/audit_reproducible_install.py --check` verifies that:

- the lockfile hash matches the versioned reproducibility contract;
- every non-comment lock entry is an exact `==` version pin;
- CI retains the pinned Python version and both operating-system install jobs;
- runtime source, scripts, CI, project metadata, lockfile and reproducibility
  contract contain no hard-coded absolute user paths.

The scan deliberately excludes test fixtures, which may contain synthetic
paths to test log redaction. This does not exclude production code or build
configuration.

Local verification on 2026-11-27 passed 8 focused tests, Ruff, mypy, the
install-contract audit, lock/fixture hash integrity verification, CI matrix
validation and `git diff --check`. A fresh hosted Ubuntu/Windows run of the
revised workflow is still required before this checklist item can be marked
COMPLETE; prior hosted green runs predate this added audit step.

The current-head Ubuntu CI run exposed that the market-data use matrix hashed
raw source bytes and therefore differed when CRLF on Windows was checked out as
LF on Linux. `scripts/market_data_use_matrix.py` now canonicalizes line endings
before hashing its inputs; `tests/test_market_data_use_matrix.py` verifies that
both line-ending forms produce identical fingerprints. This change also aligns
the checklist acceptance-artifact hash verification with canonical LF bytes.
