# Market capture and replay contract (PE-0036)

`RawQuoteArchive` remains the authorized raw Kite quote/depth capture. It also stores normalized `QUOTE`, `DEPTH`, `TICK`, and `BAR` events when the retention attestation explicitly names each permitted event kind and source. Every normalized row preserves exchange/event, receipt, availability, and optional ingest timestamps, canonical payload, source, and event digest under the durable hash chain.

`historical_bar_events` uses Kite's existing read-only historical-candle endpoint. Historical bars are stamped available when retrieved, not at their old exchange timestamp; this prevents downloaded history from masquerading as information available to a past live decision. It validates OHLCV bounds, sorts candles deterministically, and archives only with a `BAR` + `kite_historical` retention scope.

`replay_events(as_of=...)` reconstructs persisted normalized events and excludes data whose local availability time is after the replay cutoff. Trade prints can be stored as `TICK` events only when a source actually supplies them and the retention attestation permits that source. Kite historical candles are minute bars, not a complete historical trade tape; the current adapter does not claim otherwise.

Quote-based paper receipts now include the exact quote input IDs, event digests, availability times, raw archive sequence/chain references where available, the selected quote ID for a fill, and the `SIMULATED_QUOTE` fidelity tier. Fixture replays likewise attach event IDs to their quote inputs and fills. These remain simulated fills, not broker observations.

This contract records only the permissions the operator explicitly attests. It does not determine whether an exchange or provider agreement legally permits retention or redistribution.
