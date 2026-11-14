# PE-0151 — Paper order lifecycle and exact blotter quantities

## Implemented

- Execution adapters can stream immutable order updates through `ForwardSessionRunner.record_paper_order_update` while a session is open or draining. Each update has a stable update ID, timestamp, order state, requested quantity, cumulative filled quantity, exact remaining quantity, reason, and content hash.
- State transitions, timestamps, requested quantity, monotonic cumulative fills, and `requested - filled = remaining` are checked before append. Replays of the same update ID are idempotent; changed contents are rejected. Updates after session close are refused.
- Execution receipts may carry an ordered `order_updates` history. The runner records those updates and the terminal receipt, and explicitly records CANCELED orders with partial fills. An absent requested quantity stays unknown and is never inferred from filled quantity.
- `MarketableQuoteSimulator` exposes a callback-bound execution adapter; `ForwardPaperSessionService` detects and binds it to `ForwardSessionRunner.record_paper_order_update`, so the normal in-house paper simulation persists lifecycle updates while the outbox is open. Callback events are explicitly simulated and share the final receipt identity for idempotent journal reconciliation.
- The read-only cockpit blotter shows requested, filled, and remaining quantities in its current order table and preserves the per-update history, including cancel/missed reasons. Unknown quantities render as `UNKNOWN`.
- Crash recovery refreshes journal cursors after an adapter streams updates, so subsequent execution, skip, and close records extend the same durable hash chain.

## Verification

Focused bridge, runner, and lifecycle checks: 69 passed; Ruff passed. Tests cover service-integrated simulated SUBMITTED→FILLED streaming, partial fill then cancel, exact leaves, unknown quantity, invalid reconciliation, read-only history rendering, and crash recovery at each lifecycle boundary.

## Remaining evidence gap

No observed Kite forward paper session is being claimed. The implemented callback records simulator-derived updates; Kite observation, a forward session, and verification of the rendered order history against that session remain external evidence requirements. Therefore the checklist item is recorded PARTIAL.
