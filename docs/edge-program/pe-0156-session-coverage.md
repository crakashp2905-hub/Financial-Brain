# PE-0156 — Planned session coverage and denominator report

## Implemented

- Added immutable `FrozenSessionSchedule` / `PlannedSession` inputs with a stable schedule hash, exact exchange-date denominator, calendar version, and settlement deadlines. Weekend/holiday sessions cannot be mislabeled as scheduled market sessions. Special exchange sessions remain eligible through the versioned calendar.
- `session_coverage_report` reconciles the frozen schedule against the append-only `SessionDateLedger`: CLOSED, explicit NO_TRADE, INCOMPLETE, overdue MISSING, future PENDING, missing plan, and plan mismatch remain separate statuses.
- The report exposes terminal, qualified-closed, no-trade, unqualified, pending, and planned denominators. Incomplete and overdue missing outcomes count as unqualified; NO_TRADE is shown separately and is not hidden as a successful fill.
- Calendar-window reporting lists excluded weekends/holidays and tradable dates omitted from the frozen schedule, making denominator gaps visible. The report includes a canonical hash and human-readable rendering.
- `FrozenSessionScheduleRegistry` persists each cohort to a hash-chained journal. New schedules must be registered before the first exchange date, duplicate registration is idempotent, replacements are refused, and report generation resolves the schedule from the registry rather than accepting caller-supplied rows.

## Verification

`tests/test_session_coverage.py` and `tests/test_session_date_ledger.py`: 10 passed; Ruff and mypy passed. Tests cover closed/no-trade/incomplete/missing/pending outcomes, special weekend sessions, holiday/weekend exclusions, unscheduled market dates, missing ledger plans, calendar-version mismatches, pre-session schedule freeze, immutable replacement refusal, and registry restart.

## Remaining evidence gap

The registry locks the schedule before its first date, but owner-approved cohort construction and independent confirmation that it includes every intended eligible session are not configured. Daily scheduled report generation and a prospective forward cohort are also not connected. This task remains PARTIAL.
