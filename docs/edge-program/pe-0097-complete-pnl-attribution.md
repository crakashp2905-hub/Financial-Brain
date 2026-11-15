# PE-0097 — Complete P&L attribution

`reconcile_pnl_attribution()` enforces a complete, source-hashed bridge from cash-flow-adjusted NAV change to realized/unrealized price P&L, carry, cash yield, financing, tax, slippage and trading fees. It uses a declared reporting currency and rounding tolerance and refuses missing economics.

This is a strict reconciliation primitive, not yet the account's canonical automatic report. Wiring it to the durable account's event and mark projections remains outstanding, so PE-0097 stays partial.

Evidence and source hashes are in `pe-0097-complete-pnl-attribution-evidence.json`.
