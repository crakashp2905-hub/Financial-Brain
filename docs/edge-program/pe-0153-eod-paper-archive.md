# PE-0153 — End-of-session paper account close and archive

## Implemented

- `close_and_archive_paper_session` accepts effective-date, price, observation-time, and source-hash evidence for every held symbol; missing, unexpected, stale, future, or wrong-session marks refuse NAV close.
- The close routine reconciles every local paper order. Known working accepted/partial orders are canceled with fills retained and unfilled quantities released. Unsubmitted intents are rejected. UNKNOWN or unresolved SUBMITTED orders block successful close.
- It writes an immutable NAV snapshot only after cash, inventory, marks, double-entry ledger, order states, realized/unrealized return, and external flows reconcile.
- It atomically writes a canonical daily archive with exact cash, NAV, inventory quantity/cost/mark/value, order states and quantities, marks and their provenance, ledger head, and content hashes.
- `verify_prior_paper_open` checks an account reconstructed after restart against the archived NAV, cash, inventory, average cost, marks, terminal orders, and snapshot hash before the next session proceeds.
- Archive writes use unique temporary names, file flush/fsync, and atomic replacement; retries with identical state are idempotent and changed content is refused.

## Verification

`tests/test_eod_paper_archive.py`, `tests/test_paper_account.py`, `tests/test_forward_paper_account_bridge.py`, and `tests/test_forward_book_adapter.py`: 26 passed; Ruff passed. Tests include partial-fill cancellation, mark coverage/clock failures, unresolved-order refusal, archive tamper detection, exact restart NAV and inventory, and idempotent archive replay.

## Remaining evidence gap

The close workflow is implemented as a callable service but is not yet attached to a scheduled daily launcher. Mark evidence must be supplied by an entitled, authoritative close-price feed, and no observed market close/open cycle has been claimed. The item remains PARTIAL.
