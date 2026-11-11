# Chart patterns: operations guide

For the four failure modes an operator meets: corrupt data, stale quotes, a crashed scan, and a cache that must be rebuilt. The commands are offline.

## 1. Corrupt or invalid bars

- Symptom: `InvalidBars` or a quarantine count above zero.
- Cause: non-finite or non-positive prices, or an OHLC inequality.
- Action: `quarantine_rows` counts and drops bad rows and reports the reason. Do not patch values by hand. Re-ingest the source bars for that symbol and session.

## 2. Stale quotes or missing sessions

- Symptom: the status page (`views.status_page`) shows `data_freshness: STALE`.
- Cause: the last session is older than the as-of date.
- Action: check `missing_sessions` against the session calendar. Do not generate paper intents until the status shows `CURRENT`.

## 3. Crash in the middle of a scan

- Symptom: the process stopped after some bars.
- Action: rerun with `run_resumable` and the same `ScanCheckpoint` path and manifest hash. It resumes after the last committed bar. A checkpoint from a different manifest raises `CheckpointConflict`; start a new checkpoint file rather than editing the old one.

## 4. Rebuilding the result cache

- Symptom: cached results disagree with the current manifest.
- Action: `ResultCache.invalidate_except(manifest_sha)` drops entries from other manifests. Rebuild by rerunning the scan. Never edit a cache entry by hand.

## Evidence to keep

- The manifest hash and the checkpoint file for every scan.
- The output of `python -m financial_brain.patterns.demo --offline` after any change to the pattern package.
- The completion matrix check: `python scripts/chart_patterns_matrix.py --check`.
