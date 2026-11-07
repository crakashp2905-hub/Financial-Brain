# Chart patterns: evidence appendix (CP-0336)

Commands run on the final tree on Windows with Python 3.13.5. Each row is one captured run.

| Command | Exit code | Result |
|---|---|---|
| `python -m ruff check src tests` | 0 | All checks passed |
| `python -m mypy` | 0 | Success: no issues found in 39 source files |
| `python scripts/chart_patterns_matrix.py --check` | 0 | 303 of 406 items implemented, 94 incomplete, 9 not applicable with evidence |
| `python -m pytest -q tests/test_pattern_*.py` | 0 | 328 passed |
| `python -m pytest -q` (full existing suite) | 0 | 2014 passed, 1 skipped (pre-existing), 730 warnings |
| `python -m coverage report --include="*/patterns/*"` (pattern package, earlier run) | — | TOTAL 92% over the pattern package |
| `python scripts/bench_patterns.py` | 0 | Per-symbol 0.059 s; projected full scan 29 s against a 600 s budget; decision python_sufficient |
| `python -m financial_brain.patterns.demo --offline` | 0 | SYNTHETIC; status NOT_EDGE_CONFIRMED |

Protected records: `docs/audits/protected-baseline.json` (7 files) verified byte-unchanged by `tests/test_pattern_acceptance.py`.

Not covered by this appendix: Windows CI and Linux CI runs (pending on push), cross-platform event-hash equality, and any market evidence. Those are residual gates in `docs/audits/PATTERN-FINAL-GATES.md`.
