# PE-0098 — Independent EOD reconciliation

The journal-only auditor exposes recomputed positions, fill IDs and marks. `reconcile_eod_snapshot()` compares them to independent reference positions, fill receipts and source-hashed marks. `PaperEodReconciliationGate` persists each dated outcome in a hash-chained journal. A mismatch opens an incident and trips the paper account's own risk latch, so new paper intents remain blocked after restart. A clean later comparison remains blocked until a human signoff names the exact active incident and includes a source hash; signoff cannot override a current mismatch. Clearing the EOD incident does not reset the account risk latch: a separately recorded human reset is required.

Run the operational file-based reconciliation with:

```text
python -m financial_brain.forward_edge.cli eod-reconcile --account-journal <paper-account.jsonl> --reference-snapshot <independent-eod.json> --gate-journal <eod-gate.jsonl>
```

The reference JSON must contain `session_date`, timezone-aware `as_of`, `reference_positions`, `reference_fill_ids`, and `reference_marks`. Optional fields are `mark_tolerance` and `repair_signoff`. The command independently audits the local account journal before comparing it, prints the persisted gate result, exits nonzero while entries are blocked, and never submits physical orders. Replaying a session with changed evidence is rejected.

The integration accepts explicit independent OMS/venue snapshots, but the repository does not yet acquire or authenticate those snapshots or automatically schedule this command from the live-market runner. Provider/OMS adapters and daily-bundle capture therefore remain external/integration blockers, so this item remains partial.

Evidence and source hashes are in `pe-0098-eod-independent-reconciliation-evidence.json`.
