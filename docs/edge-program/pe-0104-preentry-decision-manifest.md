# PE-0104 — Pre-entry decision manifest

`DecisionManifest` content-addresses the strategy version, market snapshot, decision and earliest-entry timestamps, side, instrument, quantity, risk policy, benchmark and evidence tier. The journal requires `decision_at < recorded_at < earliest_entry_at`, refuses same/late/retroactive recording and prevents overwrites. A pinned SHA-256 golden test protects canonical serialization across platforms.

Trusted external timestamping is tracked separately; a local hash is not an independent attestation.

Evidence and source hashes are in `pe-0104-preentry-decision-manifest-evidence.json`.
