# PE-0157 — By-book opportunity, quote, cost, and P&L report

## Implemented

- Added `build_book_performance_report` to aggregate every supplied exchange session by paper-book identity.
- Session outcomes remain separately counted as CLOSED, NO_TRADE, INCOMPLETE, or UNRECORDED. Missing runner events remain in the session denominator.
- Opportunity counts are marked incomplete if any session lacks its runner opportunity-coverage record. Planned-versus-quoted instruments and candidate-signal quote coverage include explicit denominators and missing-session counts.
- Paper intents, execution receipts, unique fills, and skips are counted separately so unfilled intents are not treated as fills.
- Hashed EOD account archives are verified before use. Cumulative, flow-adjusted return, gross contribution, fees, and taxes are differenced chronologically and same-date snapshots are de-duplicated to avoid double counting.
- The human-readable report prints missing account archives and incomplete denominators instead of imputing zero or showing only filled/winning rows.
- The CLI accepts a JSON `sessions` evidence array and writes deterministic JSON and operator text into an immutable report-hash directory; identical reruns reuse it and altered output is refused.

## Verification

`tests/test_book_performance_report.py`: 6 passed; Ruff passed. Cases cover mixed winning/losing/no-trade/incomplete/unrecorded sessions, unfilled intents, cumulative archives, absent evidence, quote denominators, archive tampering, deterministic output retry, report tamper refusal, and CLI input validation.

## Remaining evidence gap

The CLI is an explicit on-demand command; the session scheduler and daily evidence launcher do not yet invoke it for every registered paper book. Its results therefore describe only the session rows and validated archives supplied by the caller. No prospective Kite session cohort exists, and cross-currency aggregation is unsupported. This task remains PARTIAL.
