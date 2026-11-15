# PE-0137 — frozen training-only ablation archive

`AblationPlan` freezes the strategy/data/protocol digests, development dates, protected final window, a complete Cartesian grid across entry rule, exit rule, holding horizon, portfolio width, and turnover limit, and an explicit maximum combination budget before evaluation. `execute_frozen_ablations()` verifies the exact dataset file digest before and after execution, rejects out-of-window schedules, and calls a supplied evaluator once for every combination/session cell. `evaluate_ablations()` accepts TRAINING outcomes only, requires every combination to use the same session/decision identities, and retains each positive, zero, and negative outcome. `AblationJournal` appends the frozen plan, every result row, summaries, and digests to a hash-chained journal and rejects retroactive replacement.

**Status: PARTIAL.** Seven focused tests, Ruff, and targeted mypy pass, including exhaustive runner coverage, frozen-data mismatch, protected-window rejection, and mid-run file mutation detection. The runner delegates each cell to a caller-supplied evaluator; the repository does not yet bind a concrete production portfolio simulator or archive a verified five-axis study. No result here is a validation-window or profitability claim.

Evidence and source hashes are recorded in `pe-0137-frozen-training-ablation-archive-evidence.json`.
