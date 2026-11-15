# PE-0197 accounting invariant evidence

Added deterministic property-style tests for directional long/short outcomes, fee monotonicity, balanced books, fill replay idempotency, conflicting duplicate IDs, and short-option expiry settlement under margin. Verification: `tests/test_accounting_properties.py` passed as part of the 78-test focused run. Ruff and mypy passed. Cash-equity shorting remains prohibited; short exposure is tested through the supported options paper ledger.
