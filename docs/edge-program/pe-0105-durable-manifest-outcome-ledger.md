# PE-0105 — Durable manifests and outcomes

`ManifestJournal(path)` now stores canonical decision manifests and session outcomes in a hash-chained `DurableJournal`. Restart revalidates manifest hashes and clock ordering. It retains zero-signal, no-trade, rejected, unknown-order and unsuccessful outcomes in the scheduled denominator, where missing rows remain `UNRECORDED`.

The forward runner has its own durable lifecycle, but it does not yet write through this canonical journal; session-date planning and outcome recording are not atomic across the two logs. PE-0105 therefore remains partial.

Evidence and source hashes are in `pe-0105-durable-manifest-outcome-ledger-evidence.json`.
