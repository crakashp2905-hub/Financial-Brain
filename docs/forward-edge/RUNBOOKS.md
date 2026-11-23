# Forward-edge paper operations runbooks

These procedures cover the repository's offline and paper-only components. They do not authorize broker orders or establish live-data entitlement. Keep the paper engine in `LIVE_PAPER`; `LIVE_CASH` and every physical broker write are permanently refused by the shared execution policy.

## Session preflight

1. Confirm the active branch, code revision, strategy parameter hashes and the frozen cohort manifest.
2. Run `python -m financial_brain.forward_edge.audit --json`. Treat any `RED` as a no-go for claims; this audit is not a market-session readiness signal.
3. Confirm the source feed, venue calendar, instrument master version, system clock and expected session segments. Do not substitute a weekday calendar for an exchange session calendar.
4. Confirm no uncleared HIGH/CRITICAL alert exists for the session. If alert storage cannot be read, fail closed and do not admit new paper intents.
5. Start only paper adapters. Record preflight and no-signal sessions as evidence; do not omit empty sessions.

## Feed outage, stale quote or expired token

1. Stop admitting new entries. Preserve the last known quote and its source and receipt times; never refresh a stale value's timestamp.
2. Record the session, instrument, source, last sequence, observed/available/received times, and outage or token-expiry cause.
3. Record affected candidates as `NO_TRADE` with the outage reason. Do not convert missing data into a fill or a zero-cost outcome.
4. Reconnect only through the configured read-only feed path. Require a fresh sequence and valid quote before resuming paper evaluation.
5. Preserve duplicate and out-of-order packet evidence for reconciliation. Do not replay an order merely because a feed or client timed out.

## Unknown order state, duplicate order or ledger mismatch

1. Halt new entries and preserve the append-only order events, broker snapshot if available, and local ledger state.
2. Mark unresolved external order state as `UNKNOWN`; do not resend or assume rejection/fill.
3. Reconcile by stable broker/order identity and event sequence. Resolve the unknown state only with a named human reconciler and evidence.
4. Compare cash, reserved funds, inventory, fees and realized/unrealized P&L. Keep a discrepancy incident until explained and cleared.
5. Resume only when duplicate exposure is ruled out and the alert is explicitly cleared. Never edit or delete historical events to force a match.

## Session close, expiry and carry

1. Enumerate every nonzero paper position at the declared session cutoff.
2. Use only marks available by cutoff and within the configured freshness limit. Missing, late or stale marks remain unresolved and downgrade completeness.
3. For expiring contracts, require an authoritative settlement/expiry source before recording a settled value. The close checker records paper disposition; it does not send close orders.
4. Carry a position only when explicit carry authorization exists and the mark is valid. Otherwise record carry blocked and require the paper workflow to close or escalate it before next-session entry processing.
5. Persist the close report and its hash, then create the daily completeness report. Missing any declared segment or close evidence means `EVIDENCE_DOWNGRADED`.

## Backup and restore drill

1. Choose explicit source paths and declare each as `file` or `sqlite`; include quote archive, OMS, paper ledger and decision manifests where they exist.
2. Create the backup outside the repository with `create_backup`. The backup manifest is written last; a directory without a valid manifest is incomplete.
3. Run `restore_drill` into a fresh, empty destination path. It refuses overwrite, verifies hashes and runs SQLite integrity checks before publishing the restore manifest.
4. Compare restored database queries and manifest hashes with the source capture. Record timestamp, revision, manifest hash, operators and differences.
5. Never test restoration over the active paper database. Production retention, offsite replication and scheduled backup automation are not yet supplied by this package.

## Evidence and incident handling

1. Preserve raw source evidence unchanged and record all timestamps with timezone and source identity.
2. Run `python -m pytest tests/test_forward_edge_tracker.py tests/test_forward_edge_alerts.py tests/test_forward_edge_session_close.py tests/test_forward_edge_completeness.py tests/test_forward_edge_backup_restore.py -q` after changing these controls.
3. Record commit, command, test result and any external blocker in the forward-edge tracker.
4. Do not describe fixture runs, local timestamps, or a paper P&L as independently attested market evidence or demonstrated profitability.

## Recovery limits and escalation

If an artifact hash fails, a SQLite integrity check fails, the clock is untrusted, a required feed is missing, an order remains unknown, or accounting does not reconcile, halt entries and retain the failed evidence bundle. Escalate unresolved broker, exchange-data, timestamp-custody or compliance requirements to the account owner/provider. This repository currently has no live order dispatch in these forward-edge modules.
