# Release changelog

## Unreleased

- Add a source-aware effective-dated instrument master contract for equities, ETFs, futures, listed options, token/ISIN history, corporate-action factors and exact optionability reconciliation with no generated strikes.
- Require Kite intraday paper trading and normalized forward-session ingestion to match the canonical, effective-dated data-rights registry; log the registry hash and reject forged, unknown or expired permissions before requesting quotes or accepting events.
- Normalize evidence hashes in the profitability-readiness snapshot across Windows and Linux checkout line endings, with a regression test.
- Add a deterministic provider/product data-use matrix covering private research, retained history, realtime algorithmic use, derivatives depth, synthetic/replay, paper simulation, redistribution, and permitted retention; unknown rights remain blocked.
- Generate per-platform release provenance and candidate-audit artifacts in CI, binding the tested source commit to JUnit/coverage hashes, portability checks, strategy-rule hashes, this changelog, and the recorded market-data rights posture.
- Keep permissions marked `UNKNOWN_RIGHTS` as explicit unresolved evidence; the manifest must not imply those uses are authorized.
- Remove the developer checkout path from the Task Scheduler wrapper usage example.
- Verify the permanent no-physical-order policy at the HTTP API boundary as well as the broker transport and paper-runner integration layers.
