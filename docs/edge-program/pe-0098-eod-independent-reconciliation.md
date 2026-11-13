# PE-0098 — Independent EOD reconciliation

The journal-only auditor now exposes recomputed positions, fill IDs and marks. `reconcile_eod_snapshot()` compares them to independent reference positions, fill receipts and source-hashed marks. A mismatch blocks new entries. Repair requires a clean subsequent comparison and a hash-backed human signoff; signoff cannot override a current mismatch.

The operational runner still needs to execute this comparison and persist the halt state and evidence bundle. Independent snapshots also require provider/OMS integration, so this item remains partial.

Evidence and source hashes are in `pe-0098-eod-independent-reconciliation-evidence.json`.
