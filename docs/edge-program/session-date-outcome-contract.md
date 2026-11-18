# Planned exchange-date outcome contract

`SessionDateLedger` is the append-only record for each exchange session the
operator schedules. A plan pins the ISO exchange date, calendar version,
tradability decision, and settlement deadline. `Calendar.special_sessions`
overrides a holiday marker, and the caller supplies the deadline for that
special session.

Every non-trading date immediately receives `NO_TRADE / NON_TRADING_DATE`.
Tradable dates may be finalized as `CLOSED`, `NO_TRADE`, or `INCOMPLETE`.
The forward runner requires a date plan when a ledger is configured, records
completed and no-trade outcomes, and records feed-loss or premature-stop
outcomes as `INCOMPLETE`. If the runner disappears, `expire_missing(now_utc)`
turns any unresolved plan past its explicit settlement deadline into
`INCOMPLETE / SESSION_RESULT_MISSING`. Outcomes and plans cannot be rewritten;
retries are idempotent, including recovery when the runner journal closed before
the date ledger was updated.

This component cannot establish that an operator supplied every eligible
exchange date. Production completeness depends on an authoritative, versioned
calendar, recording every planned date, assigning correct special-session
deadlines, and running the overdue sweep.
