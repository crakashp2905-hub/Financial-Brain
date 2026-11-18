# Paper-book NAV return contract

The paper account's append-only journal is the source for daily NAV observations. A
snapshot captures the account's cash plus every open position at its latest durable mark.
An open position without a mark makes NAV unavailable. A mark or external cash flow later
than the snapshot capture time is rejected, so a snapshot cannot silently use future data.

Deposits and withdrawals are durable cash events with stable IDs, effective timestamps,
and references. A flow cannot be added at or before the most recent snapshot; changing a
closed day's capital base requires an explicit correction process rather than rewriting
history. Each snapshot records the net external flow since the prior snapshot. Return is
`(ending NAV - opening NAV - intervening external flows) / opening NAV`.

Fills, fees, and marks affect account cash, inventory, and marked NAV through the same
OMS and double-entry ledger fold. Cash-only days therefore produce zero return absent a
flow, and late-arriving fills are reflected in the next snapshot where their durable event
and mark chronology permits. Snapshots are immutable, ordered by exchange session date and
capture timestamp, and replayed and reconciled on account recovery.

This is a paper-account accounting contract. It does not attest that marks came from a
licensed live feed, that a session was independently witnessed, or that any strategy is
profitable. Those claims require separate data lineage and forward-session evidence.
