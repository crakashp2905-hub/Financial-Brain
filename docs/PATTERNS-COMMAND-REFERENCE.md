# Chart patterns: command reference

All commands run offline and exit nonzero on failure.

| Command | Purpose | Exit codes |
|---|---|---|
| `python -m financial_brain.patterns.cli scan` | Count formations on the synthetic corpus | 0 ok, 1 error |
| `python -m financial_brain.patterns.cli list [--ticker T] [--state S] [--limit N]` | Stored records, bounded by limit (1-100) | 0 ok, 1 invalid query |
| `python -m financial_brain.patterns.cli explain EVENT_ID` | Rule-based explanation from stored fields | 0 ok, 1 unknown id |
| `python -m financial_brain.patterns.cli paper-demo` | One simulated next-session paper preview | 0 ok, 1 none found |
| `python -m financial_brain.patterns.cli audit [--matrix PATH]` | Summarise the completion matrix | 0 ok, 1 missing file |
| `python -m financial_brain.patterns.demo --offline [--out DIR]` | Full offline demo; writes report.html and export.json | 0 ok, 2 without --offline |
| `python -m financial_brain.patterns.catalogue PATH [--expect N]` | Validate the catalogue | 0 valid, 1 invalid |
| `python scripts/chart_patterns_matrix.py [--check]` | Regenerate or check the completion matrix | 0 ok, 1 stale or invalid |
| `python scripts/bench_patterns.py` | Benchmark detectors and record the native-kernel decision | 0 ok |

There are no live, broker or order flags in any command.
