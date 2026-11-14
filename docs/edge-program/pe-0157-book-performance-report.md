# PE-0157 — By-book opportunity, quote, cost, and P&L report

## Implemented

- Added `build_book_performance_report` to aggregate every supplied exchange session by paper-book identity.
- Session outcomes remain separately counted as CLOSED, NO_TRADE, INCOMPLETE, or UNRECORDED. Missing runner events remain in the session denominator.
- Opportunity counts are marked incomplete if any session lacks its runner opportunity-coverage record. Planned-versus-quoted instruments and candidate-signal quote coverage include explicit denominators and missing-session counts.
- Paper intents, execution receipts, unique fills, and skips are counted separately so unfilled intents are not treated as fills.
- Hashed EOD account archives are verified before use. Cumulative, flow-adjusted return, gross contribution, fees, and taxes are differenced chronologically and same-date snapshots are de-duplicated to avoid double counting.
- The human-readable report prints missing account archives and incomplete denominators instead of imputing zero or showing only filled/winning rows.

## Verification

`tests/test_book_performance_report.py`: 4 passed; Ruff passed. Cases cover mixed winning/losing/no-trade/incomplete/unrecorded sessions, unfilled intents, cumulative archives, absent evidence, quote denominators, and tamper detection.

## Remaining evidence gap

The report is an on-demand library function; the session scheduler and daily evidence launcher do not yet persist and invoke it for every registered paper book. Its results therefore describe only the session rows and validated archives supplied by the caller. This task remains PARTIAL pending the connected daily book registry and forward sessions.
