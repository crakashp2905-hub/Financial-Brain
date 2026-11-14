# Financial Brain paper-session runbook (Asia/Kolkata)

## Scope and safety status

This runbook is written for the Indian-market session in `Asia/Kolkata` (IST, UTC+05:30). Enter exchange dates as
`YYYY-MM-DD`; retain timezone offsets on every event timestamp. Before a real session, use the current exchange-published
trading calendar for the venue and segment, including special sessions. Do not infer a trading day from a weekday.

Today the repository has working offline fixtures and an archived-data replay path. The fixture CLI below does not read
Kite, submit orders, use a live strategy, or establish forward performance. The archived-data CLI is also a local
`RESEARCH_REPLAY` with an explicit no-strategy decision. The daily launcher that would connect observed Kite quotes,
the registered paper books, canonical account/OMS, EOD mark source, and this report is not yet wired. Therefore, use
the following as an operator rehearsal and preparation checklist; do not treat it as proof that an observed-market
paper session was run.

## 1. Before the session: prepare and preflight

1. From the repository root, activate the project's virtual environment and install its declared development tools
   once (`python -m pip install -e ".[dev]"`). Run commands below from that root.
2. Select the exchange, segment, scheduled session date, paper-book ID, cohort/attempt ID, frozen strategy version,
   risk policy, benchmark, cost schedule, permitted instruments, and initial capital. Record their version/hash in the
   session plan before consuming session data. Do not change them to improve today's outcome.
3. Confirm the current official venue calendar and local system clock. Confirm credentials are present without printing
   or copying secrets, disk space is adequate, the feed is authorized for this use, fee/tax schedule is effective for
   the date, risk limits are loaded, and physical orders remain disabled.
4. Rehearse the fixture preflight:

   ```powershell
   $env:PYTHONPATH = 'src'
   python -m financial_brain.forward_edge.cli preflight
   ```

   Expected exit code: `0`; JSON has `"ok": true`. `preflight --fail feeds` is an exception rehearsal and should return
   exit `2` with `feeds` listed under `failures`. The fixture preflight exercises the command contract only; it is not
   a probe of your Kite credentials, exchange calendar, feed, clock, or disk.

5. If any required real-session check is absent or fails, do not create a strategy intent. Record the scheduled session
   as blocked/incomplete with the reason and preserve it in the session denominator.

## 2. During the session: observe, adjudicate, and monitor

1. Use the registered read-only market-data capture and confirm quote provenance, exchange, event/receipt/availability
   timestamps, freshness, planned-instrument coverage, and data-quality counters. Keep raw capture immutable.
2. Run only a frozen, approved paper strategy through its registered adapter. Review the candidate, expected net alpha,
   cost budget, liquidity, portfolio exposure, capital reservation, and invalidation before accepting a paper intent.
   `NO_TRADE` and rejected signals are valid recorded outcomes.
3. Follow each paper order through accepted/open/partial/canceled/filled/rejected states. Count executed quantity from
   observed simulator updates; do not label a submitted quantity as filled. If acknowledgement or state is unknown,
   stop retries and reconcile the durable journal/account state before proceeding.
4. Monitor feed health, system clock, risk limits, order state, cash/margin, inventory and drawdown throughout the
   scheduled session. A stale or missing required feed is `INCOMPLETE`: block new intents, retain evidence, and do not
   rewrite it as a clean `NO_TRADE` close. Never use synthetic fixtures as a substitute for a failed live feed.
5. Keep timestamps and operator notes in IST for display, while preserving source timestamps and UTC offsets exactly.

The repository does not yet have a daily observed-Kite session launcher that performs all these steps automatically.
The offline archived-data path can be rehearsed with an authorized local archive, but it is explicitly historical replay:

```powershell
python -m financial_brain.cli forward real-paper --date 2026-11-12 --cohort offline-rehearsal `
  --archive .\session.json --journal .\session.jsonl --confirm-paper-only
python -m financial_brain.cli forward session --resume 2026-11-12:offline-rehearsal:attempt-1 `
  --journal .\session.jsonl
```

The replay makes no network request or physical order; its explicit no-strategy decision will not create a trade. The
archive must use the schema in [offline-paper-cli.md](../../forward-edge/offline-paper-cli.md), use an exchange date
matching every event in IST, and retain source/exchange provenance. Use a fresh cohort/session identity for a new
attempt. Resume only the frozen attempt; never replace an already-hashed archive with different bytes.

## 3. At close: reconcile and archive

1. Stop accepting new entries at the strategy's predeclared cutoff. Resolve all known paper orders to terminal state;
   preserve partial fills and exact residual quantities. Unknown execution state blocks a clean close.
2. Capture effective-date close marks for every held instrument, with source ID, observation timestamp and source hash.
   Do not impute missing marks, use a stale quote, or treat an absent close archive as zero P&L.
3. Reconcile order/fill state, reservations, cash, fees, taxes, inventory, realized/unrealized P&L, external flows and
   NAV. Close and archive only if the accounting checks pass. Retain no-trade, incomplete, missed-fill, rejected and
   unrecorded sessions alongside filled sessions.
4. Export the EOD archive and durable journal with the daily evidence-bundle tool. Verify hashes and signature/trust
   status on a second copy before using the artifacts in any performance report.
5. Build the by-book performance report from the complete registered session schedule, journal and verified account
   archives. Review opportunity and quote denominators, unfilled intents, missing close archives, costs, gross/net
   return and coverage before reading any aggregate return. Keep missing evidence as missing, not zero.

The EOD close/archive, portable evidence bundle, session coverage and by-book report are currently callable components;
they are not yet orchestrated by a daily launcher. Do not claim the full observed-session close path is automated.

## 4. Offline end-to-end rehearsal

This rehearsal uses only the repository's synthetic fixture pack and has deterministic exit codes:

```powershell
$env:PYTHONPATH = 'src'
python -m financial_brain.forward_edge.cli preflight
python -m financial_brain.forward_edge.cli preflight --fail feeds
python -m financial_brain.forward_edge.cli paper-run --fixture win
python -m financial_brain.forward_edge.cli close --fixture win
python -m financial_brain.forward_edge.cli report
```

Expected exit codes, in command order: `0`, `2`, `0`, `0`, `4`. The report's exit `4` is the expected
`INSUFFICIENT_EVIDENCE` result. Its fixture win and loss numbers are synthetic test values, not observed performance.
Verify the output says `physical_orders: false`, `independent_forward_sessions: 0`, and
`real_money_enabled: false`; the report must list real-quote and timestamp-custody gaps. The test
`tests/test_paper_session_runbook.py` exercises these command outcomes offline.

## Exception handling

| Condition | Required action | Session/report treatment |
|---|---|---|
| Calendar, credentials, disk, feed, data contract, risk, or system-health preflight fails | Do not start or add an intent; retain failed check and operator reason. | Blocked or `INCOMPLETE`; remain in the scheduled-session denominator. |
| Required feed stale, missing, disconnected, or clock is invalid | Stop new entries; preserve last good sample and counters; investigate before a new attempt. | `INCOMPLETE`; no clean close or zero-loss attribution. |
| Partial fill/cancel | Reconcile cumulative executed and remaining quantity; preserve every update. | Filled and residual quantities both reported. |
| Unknown acknowledgement/order state | Do not resend. Reconcile journal and the paper adapter's authoritative state. | Close blocked until state is resolved. |
| Missing/stale close mark or account mismatch | Keep account open/incomplete; obtain an authorized same-session mark and reconcile. | No NAV/P&L archive is emitted for the missing evidence. |
| Process restart | Resume the same durable session ID and verify journal/config/archive hashes before continuing. | Any mismatch refuses resume; start a new attempt only after recording the prior failure. |
| Fixture or archived replay looks profitable | Verify mode, source and evidence tier before interpretation. | Historical/synthetic evidence only; never promote it as forward alpha. |

## Current operational boundary

The fixture walkthrough is demonstrable offline. A complete observed Kite runbook cannot be accepted as an operational
procedure until one launcher connects the read-only Kite stream, universal feed/clock gates, frozen strategy adapters,
paper OMS/account bridge, EOD marks, session coverage, evidence export, and by-book report—and a prospective session
proves those links. Paper-only mode remains distinct from broker/live-cash authorization.
