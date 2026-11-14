# Unregistered strategy default (PE-0047)

`Registry.effective_status(strategy_id)` resolves any unknown ID to `SHADOW_ONLY`, and `Registry.can_place_paper_intents()` returns false for unknown IDs. The intent gate now requires the exact spec stored in the registry and a paper-capable status; a caller-created or stale spec cannot pass without registry membership. Shadow observations remain quantity-free. The existing transition rule still requires human approval before PAPER_ELIGIBLE.

This closes the registry API default. Existing strategy engines and historical paper-book writers are not all routed through this registry gate yet, so their order-intent/claim boundaries still require integration before this is system-wide enforcement.
