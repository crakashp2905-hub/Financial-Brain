# Five-desk paper operator runbook (FB-2307)

Paper only. Nothing here enables physical orders: `PHYSICAL_ORDERS_ENABLED` stays `False` and
`FB_LIVE_ORDERS` is set only by the owner, never by a script or this runbook.

## 1. Market-open preflight (before 09:05 IST)
1. Confirm the day is a business day in the loaded exchange calendar. If no calendar covers today, stop: the day cycle runs nothing.
2. Run the Kite login yourself (`scripts/kite_login.cmd`). Claude and scripts never enter credentials.
3. Check feed freshness on the health screen: quote age, gaps and reconnects per desk.
4. Check the journal balance (`Journal.check()`) and that yesterday's session gate said `SESSION_COMPLETE` or listed flagged items.
5. Any failure → let `PRE_OPEN` fail. The day cycle then halts every trading phase and records why.

## 2. Broker or feed failure during the session
- A stale quote halts the affected desk in the paper OMS. Do not resume until quotes are fresh again; `resume(desk)` is a deliberate operator step.
- Stops that trigger with no price stay `EXIT_UNRESOLVED`. Never mark them filled at the stop price.
- Do not open the live ledger or write `financial_brain.duckdb` during market hours (09:05–15:30 IST).

## 3. Kill switch
- The capital allocator trips on the daily-loss limit and refuses all new reservations.
- Only a named human clears it (`human_reset(confirmed_by=...)`). Limits are never raised to recover losses.
- Quarantine a single misbehaving desk with `quarantine(desk)` instead of stopping all desks.

## 4. Stop-trigger ambiguities
- If a bar touched both stop and target, the stop is taken (adverse-first). Do not edit this after seeing the outcome.
- If a bar opened through a level, the fill is the open, and the gap loss beyond the planned stop is kept.

## 5. End-of-day reconciliation
1. Run the `DAILY_RECONCILE` phase.
2. Reconcile open orders, fills, positions, cash buckets, MF pending items and marks.
3. The session gate reports `SESSION_COMPLETE` only when everything reconciles or is explicitly flagged unresolved. Write the flags into the day's report.
4. Desk P&L must equal consolidated P&L (`reconcile`); a mismatch is a bug, not a rounding note.

## 6. Backup, restore rehearsal and evidence custody
- After 15:30 IST, back up the DuckDB file and the raw lake to a second disk. Record the SHA-256 of each file.
- Monthly: restore the backup into a scratch folder and replay one day. The replayed journal must match entry for entry.
- Local hashes are custody evidence only; they are not independent attestation.

## 7. What this runbook does not cover
Live orders, broker approval, regulatory filings and trial-budget decisions are owner decisions outside this runbook.
