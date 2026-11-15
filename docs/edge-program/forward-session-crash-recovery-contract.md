# Forward-session crash and recovery contract

The runner journal uses the fsynced, hash-chained `DurableJournal`. Lifecycle
recovery preserves one plan, decision, risk verdict, paper intent, execution
receipt, acknowledgement and close result. Duplicate market events are
deduplicated by content hash. A persisted execution receipt is acknowledged
without invoking the simulator again.

For an order attempt without a receipt, recovery requires explicit
reconciliation. `NOT_APPLIED` is persisted before a new pending attempt; if the
process stops after that reconciliation, recovery reuses the durable proof and
does not append a duplicate reconciliation. `APPLIED` must provide its original
simulation receipt, including the simulated execution timestamp. Unknown
outcomes are never assumed to be fills.

Crash injection covers session plan/state, preflight, market event, decision,
risk, intent, outbox pending/unknown/reconciled, paper execution, outbox ack,
session close and incomplete-session records. Each restart must retain exactly
one economic execution and fill. This verifies local simulation durability; it
does not prove live broker reconciliation or venue-side exactly-once execution.
