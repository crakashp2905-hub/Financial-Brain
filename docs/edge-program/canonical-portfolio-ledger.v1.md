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

Each account snapshot also carries `pnl_promotion_gate`. It blocks account P&L promotion for
unresolved legacy positions, incomplete position projection, or missing opening cash. The
migration and audit CLIs surface the same gate. `ELIGIBLE_FOR_REVIEW` only means these local
book-integrity blockers are absent; it does not establish positive returns, independent
attestation, statistical validity, or automatic strategy promotion. An unresolved exit can
only be resolved after receipt-backed execution facts are recorded and the source audit is
re-run; changing a label or synthesizing a fill cannot release the gate.

`append_execution_receipt_resolution(account_id, unresolved_event_id, receipts)` is the
explicit receipt path. It requires one or more `BROKER_EXECUTION_RECEIPT` fills with broker
order/trade IDs, content hashes, matching instrument, closing side and total quantity equal
to the unresolved position. Local-paper fills and partial receipt sets do not clear the
unresolved state. The method binds the facts but cannot authenticate the broker or receipt
bytes; the operator must retain and verify the original receipt artifact. The current 51
missing-exit records have no such receipts and remain blocked.

## Legacy paper-book migration

`financial_brain.portfolio.legacy_migration` maps persisted rows from the existing intraday,
swing and forward-options paper books. The `scripts/migrate_legacy_paper_books.py` command
opens those DuckDB sources read-only and writes a separate SQLite ledger. Intraday rows require the
recorded trade ID, side, quantity and entry; closed rows retain their stored exit and modeled
charges. Swing rows retain their stored entry/exit/last-close marks and label modeled cost
rates as estimates. Legacy signal snapshots without a fill remain unresolved.

Options-forward outcomes are joined to their immutable pre-entry intent. `CLOSED` outcomes
map the persisted contract, quantity, two-sided quote entry/exit and modeled charges. A
`NO_EXIT_QUOTE` outcome maps the recorded entry plus an unresolved position; outcomes with
no entry fill or a failed cost/liquidity gate create no position. Outcomes that do not contain
enough contract/quantity provenance are rejected instead of guessed. The
`options_paper_forward.duckdb` source is optional and opened read-only.

The migration was run against the local `paper_live.duckdb` and `paper_book.duckdb`. The
sanitized snapshot in `canonical-migration-audit.v1.json` fingerprints both source databases
and verifies every expected event payload against the local output ledger. It records 1,631
intraday rows and 6,334 swing rows, all 23,479 derived events verified, and zero rejected
source rows. The local output is `data/canonical_portfolio.sqlite` and is git-ignored; source
databases were not modified. The audit report contains no ticker, trade price or P&L values.

Re-run the import and audit from the repository root after updating the source books:

```powershell
python scripts/migrate_legacy_paper_books.py --data-dir data --ledger data/canonical_portfolio.sqlite
python scripts/audit_canonical_portfolio_migration.py --data-dir data --ledger data/canonical_portfolio.sqlite `
  --output docs/edge-program/canonical-migration-audit.v1.json
```

The data includes 51 intraday records without an exit and five legacy swing rows that cannot
be fully reconstructed. Those rows remain unresolved. The audit reports 406 open positions
across 18 strategy/run books; replay and challenger books overlap, so that count is not a
unique live holdings count. All 18 books lack a recorded opening cash balance, so the cash
view is net event flows only. Four books have incomplete position projections.

The source snapshot used for the audit had intraday and swing records. No options-forward
database was present in that snapshot, and no ETF-specific or mutual-fund order book was
identified in the scanned databases. No entries were inferred for those desks, and no live
holdings snapshot was treated as a fill. The options adapter is covered by fixture databases;
it does not claim a real options book has been migrated until a source exists and the audit
verifies the source-to-ledger mapping.

## Verification

`tests/test_canonical_portfolio_ledger.py`, `tests/test_legacy_portfolio_migration.py`,
`tests/test_paper_options_runner.py` and `tests/test_paper_options_forward.py` cover equity,
ETF, option and MF units; source idempotency; conflicting and atomic imports; timezone-aware
as-of marks; source-specific mapping; exact payload verification; missing provenance; and
unresolved positions. Twenty-one targeted tests pass. The migration audit is a local
paper-book reconciliation only, not a broker reconciliation or performance evaluation.

## Migration status and limits

The in-repository code now includes source adapters for the two local equity paper books and
persisted forward-options outcomes. Broader multi-asset migration remains partial: no
ETF-specific trading or mutual-fund order-book source was identified, and no options-forward
database was present in the captured data directory. Opening cash, source-specific charge
verification and external account reconciliation are also missing. Any new source row without
fill and mark provenance must remain unresolved.

The current projection also does not model settlement calendars, taxes, corporate actions,
FX conversion, margin, exercise/assignment, option expiry, fund allotment/redemption pending
states, or external broker reconciliation. It is paper-only infrastructure and must not be
used as a live-accounting or readiness claim.
