# PE-0107 — Local hash-chained evidence

The local default uses the existing fsynced append-only `DurableJournal`. Canonical manifests persist in this chain; a byte-level edit is detected on reopen. This establishes local integrity only: without a trusted external receipt, the evidence remains LOCAL_ONLY.

Evidence and source hashes are in `pe-0107-local-hash-chain-evidence.json`.
