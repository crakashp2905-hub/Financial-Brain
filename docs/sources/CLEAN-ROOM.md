# Clean-room rules for external algorithm ideas (ALG-006)

1. Every implementation is written from the documented trading concept, not copied from a Pine or MQL5 source file.
2. No third-party code excerpt larger than a single formula appears in `src/financial_brain/strategies/`.
3. Each module's docstring names the concept and the registry IDs it draws on, and the authorship is Financial-Brain's own.
4. Vendor performance figures are never reproduced as results. They are recorded as `SOURCE_CLAIM` in the registry only.
5. External pages are passive citations. The runtime works with no network and no source site reachable (ALG-011).
6. A licence is recorded only when independently established, in the registry field `licence_reference`. Without it the rights status is `reference_only`.

Tests that enforce these rules: `tests/test_algo_registry.py` (provenance, rights, no Pine or MQL5 markers in runtime modules, offline import).
