# Chart patterns: agent handoff

Procedure for a coding agent continuing this work:

1. Read `docs/audits/chart-patterns-master-checklist.md` and `docs/audits/chart-patterns-completion.json`.
2. Pick an `incomplete` row in stage order. Do not skip a stage's safety dependencies.
3. Implement it with tests, then add its evidence to `scripts/chart_patterns_matrix.py` and regenerate.
4. Never call a broker or data API, collect credentials, run a protected trial, edit protected records,
   or enable live trading. Use fixtures and mock boundaries when real inputs are missing.
5. Gates: pattern tests, ruff, mypy, the protected-baseline test and the matrix `--check` must pass before a commit.
