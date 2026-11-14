# Live tick and next-day bar divergence audit (PE-0043)

`compare_live_ticks_to_historical_bar` builds OHLC from archived `TICK` events inside the historical `BAR` interval, compares it to the later bar using a frozen basis-point tolerance, and appends an idempotent `LIVE_HISTORICAL_DIVERGENCE` record to a durable audit journal. It rejects non-Kite-historical bars, unsupported intervals, same-day retrieval, or a bar available before the compared live ticks.

The comparison retains tick and bar hashes, source labels, interval, per-field live/historical values, signed deltas and absolute basis-point deltas. A mismatch is marked `DIVERGENT`; a match is `MATCH_WITHIN_TOLERANCE`. Both remain `UNVERIFIED` until a separate adjudication process approves them. The comparator never adjusts either data series.

Callers must supply ticks and bars loaded through the rights-gated archive with the required replay/persistence actions. The source labels and source tag are auditable assertions, not independently verified provider credentials. Synthetic tests establish deterministic behavior; they do not establish real feed divergence rates or validate a provider's next-day history.
