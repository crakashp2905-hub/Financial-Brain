# Order and cash ledger authority audit

**Checklist:** PA-01-05 — PARTIAL. The repository has a canonical portfolio projection, but it has not yet unified order/cash authority across all paper and OMS paths.

## Current scoped owners

| State | Current authority | Boundary |
|---|---|---|
| Broker order lifecycle | `src/financial_brain/execution/oms/journal.py` and broker OMS | Broker acknowledgements and broker reconciliation are distinct from simulated paper fills. |
| Prospective simulated order lifecycle | `ForwardSessionRunner` durable event journal | Owns session intents and simulated receipts; its accounting projection is gross-only and not transactionally committed with canonical portfolio accounting. |
| Consolidated portfolio projection | `CanonicalPortfolioLedger` | Append-only event-derived account projection; does not authenticate broker state, and migration remains incomplete. |
| Legacy intraday/swing/options/MF source books | Individual source databases and book APIs | Migration inputs; no silent fill inference. They are not yet mechanically prevented from being treated as current book views. |

The ownership map is scoped by `(scope_id, domain)`, so separate research books are not automatically duplicate accounts. However, the map is declarative: it does not force every writer to claim an account/domain lease. `state-ownership.v1.json` explicitly says the forward session has no canonical net ledger and no durable canonical position snapshot. The ledger migration report further records missing opening cash across 18 migrated books, unresolved position rows, and absent local ETF/options/MF source books.

## Required implementation to close the gap

1. Define a stable account/sleeve identity and an enforced unique writer for each authoritative `(account_id, domain)`; reject a second writable ledger claim.
2. Route OMS fills, forward paper receipts and approved legacy migrations through one canonical event-ingress API with source ID, evidence class, idempotency key and atomic cash/inventory effects.
3. Keep legacy books as source systems or read-only projections after migration; prevent dashboards and decision code from adding both the source balance and canonical balance for the same account.
4. Reconcile opening cash, orders, fills, units, charges, settlements and marks after every event; unresolved values must remain flagged and block completion.
5. Add restart, duplicate-source, overlapping-book and concurrent-writer tests proving one account balance and one position inventory are emitted.

## Verification and current conclusion

- `tests/test_canonical_portfolio_ledger.py`, `tests/test_legacy_portfolio_migration.py`, `tests/test_forward_edge_ledger_reconcile.py`, and the forward-runner restart reconciliation test: 22 passed.
- The tests establish append-only canonical projection, idempotent legacy migration and forward-runner restart behavior. They do **not** establish one transactional ledger across OMS, forward sessions and legacy books.
- Result: **PARTIAL**. No duplicate account ownership claim is established as valid; runtime exclusivity and end-to-end unification remain unimplemented.
