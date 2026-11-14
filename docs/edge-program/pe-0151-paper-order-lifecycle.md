# PE-0151 — Paper order lifecycle and exact blotter quantities

## Implemented

- Execution adapters can stream immutable order updates through `ForwardSessionRunner.record_paper_order_update` while a session is open or draining. Each update has a stable update ID, timestamp, order state, requested quantity, cumulative filled quantity, exact remaining quantity, reason, and content hash.
- State transitions, timestamps, requested quantity, monotonic cumulative fills, and `requested - filled = remaining` are checked before append. Replays of the same update ID are idempotent; changed contents are rejected. Updates after session close are refused.
- Execution receipts may carry an ordered `order_updates` history. The runner records those updates and the terminal receipt, and explicitly records CANCELED orders with partial fills. An absent requested quantity stays unknown and is never inferred from filled quantity.
- The read-only cockpit blotter shows requested, filled, and remaining quantities in its current order table and preserves the per-update history, including cancel/missed reasons. Unknown quantities render as `UNKNOWN`.
- Crash recovery refreshes journal cursors after an adapter streams updates, so subsequent execution, skip, and close records extend the same durable hash chain.

## Verification

Focused runner, crash-matrix, lifecycle, and cockpit checks: 77 passed; Ruff passed. Tests cover partial fill then cancel, exact leaves, unknown quantity, invalid reconciliation, live adapter callback journaling, read-only history rendering, and crash recovery at each lifecycle boundary.

## Remaining evidence gap

The repository provides the callback and journal-to-cockpit adapter contract, but no observed Kite paper session is being claimed. The production paper execution adapter still must call the update callback with its actual per-event paper order facts, and a forward session must demonstrate the rendered feed. Therefore the checklist item is recorded PARTIAL.
