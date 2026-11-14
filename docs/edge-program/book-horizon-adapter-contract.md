# Daily paper-book horizon adapter

`forward_position_events` reads persisted `paper.book.positions` rows and emits
`BOOK_POSITION` events for the forward runner. It is called with an exchange
session date and the same injected session clock used by the runner. It is a
read-only daily-state bridge; it is not called by the intraday poll loop and it
never creates a fill.

The persisted row's `horizon` and completed `held` sessions are authoritative.
When a row has no entry fill yet, the event remains `ENTRY_PENDING`. Positions
with fewer completed sessions than their horizon remain `ACTIVE` across dates.
An open row at its horizon is retained as `EXIT_DUE` for end-of-session
reconciliation rather than being silently deleted or forced out by an
intraday callback. Future-dated marks, invalid horizons and corrupted holding
counts fail closed.

The adapter reports local paper-book state only. It does not establish that the
book's source prices were licensed, independently attested, or executable.
