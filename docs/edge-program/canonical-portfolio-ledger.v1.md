# Canonical paper portfolio ledger v1

`financial_brain.portfolio.canonical_ledger` is an append-only, SQLite-backed event journal
and deterministic as-of position projection for a paper portfolio. Each event records its
account and sleeve, source system and event ID, caller-provided source-row SHA-256, event
time, currency, instrument class, evidence class and the fields required by its event type.

The schema accepts cash flows, fills, fees, marks and unresolved legacy position snapshots.
Fills use signed BUY/SELL semantics, fractional units for NAV-based funds and an explicit
contract multiplier for options. Average cost and realized/unrealized gross P&L are computed
from the ordered fill and mark events. It rejects conflicting source-ID replays and imports
batches atomically. As-of reads normalize timestamps to UTC and do not rewrite earlier marks.

An `UNRESOLVED_POSITION` appears in the snapshot's unresolved list, changes neither cash nor
the canonical position projection, and forces `projection_complete=false`. This flag only
means the local event projection has no unresolved positions and has marks for all open
positions. It is not broker reconciliation or a claim that cash/charges match an external
account. A row with no explicit execution is never silently turned into a fill.

## Verification

`tests/test_canonical_portfolio_ledger.py` covers equity, ETF, option and MF units; source
idempotency; conflicting and atomic imports; timezone-aware as-of marks; missing provenance;
and unresolved positions. This is schema/reducer evidence on synthetic fixtures only.

## Migration status and limits

This increment creates the shared event format and reducer; it does **not** yet migrate the
local legacy ledgers. The repository has no checked-in runtime book databases, and several
legacy sources store strategy outcomes or position snapshots rather than execution receipts.
Source-specific adapters still need to map verified trade IDs, quantities, prices, charges,
contract identity, book/sleeve and evidence mode into events. Any source row without those
facts must be retained as unresolved and reconciled against its original mark basis before a
book can be called migrated.

The current projection also does not model settlement calendars, taxes, corporate actions,
FX conversion, margin, exercise/assignment, option expiry, fund allotment/redemption pending
states, or external broker reconciliation. It is paper-only infrastructure and must not be
used as a live-accounting or readiness claim.
