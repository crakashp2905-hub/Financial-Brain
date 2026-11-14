# Forward paper execution adapter contract

`MarketableQuoteSimulator` connects the forward session runner to the existing
conservative execution model. It is local simulation only and has no broker API.

The decision includes an instrument, side, positive quantity and optional limit.
The runner supplies the immutable decision timestamp. The adapter requires a
configured positive tick size, a valid two-sided quote with positive displayed
size, and a quote available no earlier than decision time plus configured
latency. It ignores last-trade-only, crossed, invalid and empty quotes. The
execution model applies adverse tick rounding and caps a fill at displayed
size; missing executable quotes produce an explicit `MISSED` result.

Every outcome has a stable intent-derived simulated receipt ID and explicit
`SIMULATED_QUOTE` fidelity and the hashes of all valid quote events considered.
A fill contains a stable fill ID, quantity, price, quote availability timestamp
and the exact normalized quote event SHA-256 that produced it. The runner
validates and journals the receipt before acknowledging the outbox. Exceptions
remain unknown side effects and require reconciliation before retry. The
adapter itself is deterministic and side-effect free, so a replay does not
submit or duplicate a physical order.

The caller remains responsible for supplying current tick metadata and a feed
health gate. Fees, portfolio accounting and authorized live-market observation
are separate integrations; this adapter does not claim live execution fidelity
or profitability.
