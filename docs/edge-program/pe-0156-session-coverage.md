# PE-0156 — Planned session coverage and denominator report

## Implemented

- Added immutable `FrozenSessionSchedule` / `PlannedSession` inputs with a stable schedule hash, exact exchange-date denominator, calendar version, and settlement deadlines. Weekend/holiday sessions cannot be mislabeled as scheduled market sessions. Special exchange sessions remain eligible through the versioned calendar.
- `session_coverage_report` reconciles the frozen schedule against the append-only `SessionDateLedger`: CLOSED, explicit NO_TRADE, INCOMPLETE, overdue MISSING, future PENDING, missing plan, and plan mismatch remain separate statuses.
- The report exposes terminal, qualified-closed, no-trade, unqualified, pending, and planned denominators. Incomplete and overdue missing outcomes count as unqualified; NO_TRADE is shown separately and is not hidden as a successful fill.
- Calendar-window reporting lists excluded weekends/holidays and tradable dates omitted from the frozen schedule, making denominator gaps visible. The report includes a canonical hash and human-readable rendering.

## Verification

`tests/test_session_coverage.py` and `tests/test_session_date_ledger.py`: 9 passed; Ruff passed. Tests cover closed/no-trade/incomplete/missing/pending outcomes, special weekend sessions, holiday/weekend exclusions, unscheduled market dates, missing ledger plans, and calendar-version mismatches.

## Remaining evidence gap

The report consumes a caller-supplied frozen schedule; there is no registered schedule authority preventing an operator from omitting planned dates before generation. A daily automation and prospective forward cohort are not yet connected. This task remains PARTIAL.
