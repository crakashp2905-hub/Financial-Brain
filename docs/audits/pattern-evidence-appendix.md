# Chart patterns: evidence appendix (CP-0336)

Commit under test: base `b4fe829` (waves 1 to 8). The wave-9 work is committed on top of it. Its commit hash is recorded in `git log` and in `docs/CHANGELOG-PATTERNS.md`, because a file cannot name the commit that contains it.

Platform: Windows 11, Python 3.13.5. Each row is one captured run on the wave-9 working tree before its commit.

| Command | Exit code | Result |
|---|---|---|
| `python -m ruff check src tests` | 0 | All checks passed |
| `python -m mypy` | 0 | Success: no issues found in 43 source files |
| `python scripts/chart_patterns_matrix.py --check` | 0 | 374 of 406 items implemented, 23 incomplete, 9 not applicable with evidence |
| `python -m pytest -q tests/test_pattern_*.py` | 0 | 461 passed (includes 133 wave-9 tests) |
| `python -m pytest -q` (full existing suite) | 0 | 2137 passed, 1 skipped (pre-existing), 730 warnings, 618 s |
| `python scripts/bench_patterns.py` | 0 | Per-symbol 0.059 s; projected full scan 29 s against a 600 s budget; decision python_sufficient (base commit) |
| `python -m financial_brain.patterns.demo --offline` | 0 | SYNTHETIC; status NOT_EDGE_CONFIRMED |
| `python scripts/build_pattern_fixtures.py` | 0 | 18 family examples written and checked |
| `fb patterns paper-demo` (`financial_brain.cli:main`) | 0 | Paper-only preview printed |

Protected records: `docs/audits/protected-baseline.json` (7 files) verified byte-unchanged by `tests/test_pattern_acceptance.py`.

Not covered by this appendix: Windows CI and Linux CI runs (pending on push), cross-platform event-hash equality, and any market evidence. Those are residual gates in `docs/audits/PATTERN-FINAL-GATES.md`.
