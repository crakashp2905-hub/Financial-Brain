# PE-0130 — passive-fill and queue-position scenarios

`queue_fill_scenarios()` reports no-fill, low/mid/high queue-ahead, and full-fill outcomes for a frozen passive-intention population. Each intention requires a bounded queue-ahead estimate and trade-through quantity. The report exposes fill quantities/fractions, gross P&L, costs, and net P&L per scenario. Queue-position bounds use both ends of the supplied interval and select economic lower/upper outcomes without assuming more fills always improve P&L.

**Status: PARTIAL.** Ten focused tests and Ruff pass. The model requires queue interval and trade-through inputs from a caller; no rights-gated order-book/trade evidence is attached. Partial-fill gross and costs scale linearly from full-fill totals, so changing adverse selection, minimum charges, or intratrade risk are not represented. It is a scenario report, not a queue-fill observation, and is not yet a promotion gate.

Evidence and source hashes are recorded in `pe-0130-queue-fill-boundaries-evidence.json`.
