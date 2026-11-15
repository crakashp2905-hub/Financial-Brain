# PE-0141 — rejected-opportunity shadow book

`simulate_rejected_shadow_book()` simulates only explicitly rejected candidate intents. Marketable-limit entries consume shared displayed ask/bid depth at executable prices within the frozen limit; a shared quote event cannot be reused across candidate orders. Buys respect a per-intent virtual cash budget. Later closes use the opposite executable side, and FIFO lots produce hypothetical gross P&L with explicit modeled costs. No fills, partial entries, and unresolved closes remain explicit. `ShadowBookJournal` is append-only and immutable by book identity.

Every report is permanently labeled `EXPLORATORY_SHADOW`, sets `observed_cash_pnl=false`, and sets `promotion_eligible=false`, even when control IDs are supplied. Registered control IDs are recorded but do not authorize promotion without a separate registered trial and review.

`simulate_rejected_shadow_book_from_archive()` now consumes normalized quote events from the existing `RawQuoteArchive`. It verifies the archive, requires its `local_read` permission and source/event-kind scopes, identifies each quote by archive sequence and event digest, and uses `available_utc` as the observation clock. This closes the local data-path gap for quote inputs; it does not create rejected decisions, independently authenticate operator-supplied retention attestations, or provide portfolio cash/margin controls.

**Status: PARTIAL.** Focused shadow-book, raw-archive, and F07 tests pass; Ruff passes. The local authorized quote archive can feed the simulation, but rejected decisions and modeled cost assumptions remain caller-supplied; no strategy rejection stream is connected. The book does not yet enforce shared portfolio cash/margin across candidate strategies or construct registered matched controls. It is not a cash account or execution record.

Evidence and source hashes are recorded in `pe-0141-rejected-opportunity-shadow-book-evidence.json`.
