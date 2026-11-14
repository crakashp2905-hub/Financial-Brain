# PE-0141 — rejected-opportunity shadow book

`simulate_rejected_shadow_book()` simulates only explicitly rejected candidate intents. Marketable-limit entries consume shared displayed ask/bid depth at executable prices within the frozen limit; a shared quote event cannot be reused across candidate orders. Buys respect a per-intent virtual cash budget. Later closes use the opposite executable side, and FIFO lots produce hypothetical gross P&L with explicit modeled costs. No fills, partial entries, and unresolved closes remain explicit. `ShadowBookJournal` is append-only and immutable by book identity.

Every report is permanently labeled `EXPLORATORY_SHADOW`, sets `observed_cash_pnl=false`, and sets `promotion_eligible=false`, even when control IDs are supplied. Registered control IDs are recorded but do not authorize promotion without a separate registered trial and review.

**Status: PARTIAL.** Twenty focused shadow-book and F07 tests pass; Ruff passes. Quote streams, rejected decisions, and modeled cost assumptions are caller-supplied; no live quote journal or strategy rejection stream is connected. The book does not yet enforce shared portfolio cash/margin across candidate strategies or construct registered matched controls. It is not a cash account or execution record.

Evidence and source hashes are recorded in `pe-0141-rejected-opportunity-shadow-book-evidence.json`.
