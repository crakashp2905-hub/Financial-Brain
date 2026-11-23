# Provider/product data-use matrix audit — 2026-11-23

**Checklist:** `PA-02-01`  
**Disposition:** `COMPLETE` for creating and validating the requested matrix. This does not authorize any data use.

`data-use-matrix.v1.json` is generated from the repository's 17-source inventory. It expands the 27 declared provider/product pairs across the eight requested dimensions: private research, retained history, real-time algorithmic use, derivatives depth, synthetic/replay, paper simulation, redistribution, and permitted retention. The CI freshness check verifies the matrix is deterministic and synchronized with the inventory.

All configured provider/product uses remain `UNKNOWN_RIGHTS`. Products without a configured provider are `NOT_CONFIGURED`. Synthetic replay is separately labeled `INTERNAL_FIXTURE_ONLY`, contains no provider data, and grants no provider rights. The existing operational permissions register was not upgraded.

Verification: `python -m pytest tests/test_data_use_matrix.py -q` (3 passed with a workspace-local short pytest temp path on Windows); `python scripts/data_use_matrix.py --check`; `python scripts/profit_readiness.py --check`; scoped Ruff; `git diff --check`.
