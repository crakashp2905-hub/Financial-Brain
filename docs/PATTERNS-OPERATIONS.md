# Chart patterns: operations

- Startup health: `financial_brain.patterns.demo.health(catalogue_path, out_dir)` checks the catalogue, offline mode
  and the output folder. It modifies nothing.
- Offline mode is the default. An unknown `PATTERN_DEV_MODE` value fails closed.
- Network denial: the demo disables socket connections for its own process. Tests verify this in a subprocess.
- Corrupt artifacts: model loads verify SHA-256 and fail closed. Backups verify checksums before restore.
- Stale data: `freshness()` returns STALE or INVALID so callers abstain.
- Resource use: `scripts/bench_patterns.py` records timing on the machine that runs it. Re-run it after changes.
- Logs and redaction: `agent.redact` removes tokens, long account-like numbers and local file paths before export.
