# PE-0137 — frozen training-only ablation archive

`AblationPlan` freezes the strategy/data/protocol digests, development dates, protected final window, a complete Cartesian grid across entry rule, exit rule, holding horizon, portfolio width, and turnover limit, and an explicit maximum combination budget before evaluation. `evaluate_ablations()` accepts TRAINING outcomes only, rejects observations outside the development window or on/after the protected final start, requires every combination to use the same session/decision identities, and retains each positive, zero, and negative outcome. `AblationJournal` appends the frozen plan, every result row, summaries, and digests to a hash-chained journal and rejects retroactive replacement.

**Status: PARTIAL.** Thirteen focused tests and Ruff pass, including existing horizon and bounded-search tests. The archive evaluates and records a complete supplied training outcome matrix, but it is not yet an adapter that executes the portfolio simulator for every candidate. The declared data digest is caller-supplied and the current repository does not contain a verified study run across all five axes. No result here is a validation-window or profitability claim.

Evidence and source hashes are recorded in `pe-0137-frozen-training-ablation-archive-evidence.json`.
