# Changelog: chart-pattern module

## Unreleased (engineering waves 1-8)

- Offline guard (`PATTERN_DEV_MODE`, fail-closed), import boundary test.
- Pattern contracts: spec, event, lifecycle, evidence tiers, canonical hashing, decision and formation-start clocks.
- Causal pivots, streaming pivots, geometry primitives and tolerance policies.
- Causal detectors: rectangle, triangles and wedges, flags, double extrema, head and shoulders. Prefix invariance tested for every detector.
- Catalogue validator for the 72 Nison and Bulkowski rows, JSON Schema file, reference index, truthful per-row inventory.
- Market-data contracts (timestamps, sessions, identity, splits, dividends, freshness, entitlements) and candle wrappers over existing library features.
- Study protocol and controls: spec hashing, budget guard, purged splits, FDR, placebo, residuals, ablations, attribution.
- Paper economics and risk: intents, costs, fills, ledger identity, risk codes, gap-stress and sizing rules, kill switch.
- Ranking, four separate scores, trader recommendations, escaped offline HTML report and JSON export.
- Shadow learning registry (immutable versions, rollback, fail-closed corruption), descriptive model, read-only agent with validation, refusals and redaction.
- Offline demo, CLI (`scan`, `list`, `explain`, `paper-demo`, `audit`), health check, checksummed backup and restore, sign-off that never claims market edge.
- Completion matrix: 303 of 406 checklist items implemented, 94 incomplete, 9 not applicable with benchmark evidence.

Known gaps: see `docs/audits/PATTERN-FINAL-GATES.md` and the incomplete rows in `docs/audits/chart-patterns-completion.json`.
