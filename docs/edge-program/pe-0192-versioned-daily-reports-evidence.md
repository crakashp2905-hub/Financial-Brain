# PE-0192 — One schema for daily JSON/CSV/HTML reports

## Acceptance

Produce daily JSON, CSV, and HTML reports from one versioned report schema. Totals must match across formats and OSes.

## Implementation and verification

`forward_edge.daily_report` verifies the EOD account archive and hash-chained runner journal, requires a matching session plan, reconciles equity to cash plus marked inventory, and constructs schema v1.0 once. `render_daily_reports` serializes the same totals to stable UTF-8 JSON, LF-delimited CSV, and escaped HTML. Each report carries a content hash. The `daily-export` CLI command writes all three siblings from the same object and labels physical orders false.

Verification: `tests/test_daily_report_formats.py` and `tests/test_forward_edge_f13.py` — 44 passed; Ruff, targeted mypy, and source-inventory checks passed. The format-parity test parses all three outputs and compares every total, and repeated renders are byte-identical.

## Limits

Exports require a valid EOD paper archive and hash-valid runner journal with a dated session plan. These reports describe simulated/paper evidence; they do not claim actual exchange execution or profitability. Atomic replacement is per file; consumers should treat the report hash and sibling set as one publication unit.
