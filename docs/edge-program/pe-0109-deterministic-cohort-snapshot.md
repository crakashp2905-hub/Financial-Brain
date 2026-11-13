# PE-0109 — Deterministic cohort snapshot and replay verification

Cohort bundles now contain a deterministic manifest/schedule/denominator snapshot, explicit scheduled session IDs, and custody receipts with their computed status. Optional ordered order and equity artifacts are exported as JSONL. A second machine can re-run the same inputs and compare the resulting hashes; the verifier reports custody status differences under its configured trust roots. Duplicate and out-of-range schedule dates are rejected.

**Status: DONE for snapshot export and replay verification.** The tests demonstrate byte-stable file verification, matching and mismatching replay hashes, and custody mismatch reporting. This does not establish independent market data, observed forward trading, or profitability. PE-0103 and PE-0106 still govern authoritative exchange calendars and production trust roots.

Evidence and source hashes are in `pe-0109-deterministic-cohort-snapshot-evidence.json`.
