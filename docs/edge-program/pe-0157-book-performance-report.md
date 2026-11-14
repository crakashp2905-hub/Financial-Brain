# PE-0157 — By-book opportunity, quote, cost, and P&L report

## Implemented

- Added `build_book_performance_report` to aggregate every supplied exchange session by paper-book identity.
- Session outcomes remain separately counted as CLOSED, NO_TRADE, INCOMPLETE, or UNRECORDED. Missing runner events remain in the session denominator.
- Opportunity counts are marked incomplete if any session lacks its runner opportunity-coverage record. Planned-versus-quoted instruments and candidate-signal quote coverage include explicit denominators and missing-session counts.
- Paper intents, execution receipts, unique fills, and skips are counted separately so unfilled intents are not treated as fills.
- Hashed EOD account archives are verified before use. Cumulative, flow-adjusted return, gross contribution, fees, and taxes are differenced chronologically and same-date snapshots are de-duplicated to avoid double counting.
- The human-readable report prints missing account archives and incomplete denominators instead of imputing zero or showing only filled/winning rows.
- The CLI accepts a JSON `sessions` evidence array and writes deterministic JSON and operator text into an immutable report-hash directory; identical reruns reuse it and altered output is refused.
- The `daily-paper-cycle` launcher now finalizes every registered book in a frozen plan and feeds each generated, verified EOD account archive and the matching durable runner journal into this report. Explicit `history_sessions` are retained in the report denominator and are never inferred.
- `daily-paper-preflight` validates the whole daily input set and historical report rows without account mutation before an operator or scheduler runs the close cycle.
- The Windows `paper_daily_finalize.cmd` wrapper provides an explicit scheduler entry point with preflight, logging, manifest requirement, and nonzero failure propagation; the scheduled task itself is not installed.
- The `daily-paper-manifest` command derives exactly one terminal strategy/account session per frozen-plan book from durable runner journals and emits an immutable hash-checked manifest; it refuses ambiguous matches.
- The cycle manifest and retry contract are documented in [daily-paper-cycle.md](../forward-edge/daily-paper-cycle.md).

## Verification

`tests/test_book_performance_report.py`: 6 passed; Ruff passed. Cases cover mixed winning/losing/no-trade/incomplete/unrecorded sessions, unfilled intents, cumulative archives, absent evidence, quote denominators, archive tampering, deterministic output retry, report tamper refusal, and CLI input validation.

## Remaining evidence gap

The launcher is an explicit on-demand command; no OS scheduler invokes it for every market session. Its historical report denominator depends on caller-supplied `history_sessions`. No prospective Kite session cohort exists, and cross-currency aggregation is unsupported. This task remains PARTIAL.
