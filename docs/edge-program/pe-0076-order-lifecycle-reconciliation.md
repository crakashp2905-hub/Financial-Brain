# PE-0076 simulated order outcomes and lifecycle reconciliation

Execution exposes explicit missed/zero-fill, partial-fill, price-band rejection, outage/no-trade, and circuit/auction halt outcomes. The durable OMS supports cancel and expiry terminal states. A partial fill followed by expiry retains the filled quantity and cost basis, releases only the unspent reservation, and replays to the same cash, inventory, and terminal order state.

The task is `DONE` for the in-house paper simulator and account lifecycle. This does not model exchange-specific rejection frequencies or venue halt behavior from observed market data.
