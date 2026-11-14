# PE-0154 — Feed and clock failure closeout

## Implemented

- `ForwardRunConfig.require_feed_health` makes feed-health evidence mandatory for an observed paper session while preserving historical replay configuration identity when the flag is false.
- Required but absent feed health, stale quotes/heartbeats, gaps, disconnections, dropped updates, future timestamps, and other unsafe feed status block the strategy/risk/execution callbacks and produce a durable `SESSION_INCOMPLETE` record with the health evidence and reason codes. No `SESSION_CLOSED`, P&L success, or zero-loss completion is written.
- The session plan is now persisted before the decision clock is read. A naive/untrusted decision clock creates durable incomplete evidence and stops before decision or order creation.
- A clock failure after an order intent exists leaves the outbox explicitly UNKNOWN and without execution acknowledgment or session close; the system requires reconciliation instead of claiming no loss.
- Existing account, position, and risk records remain untouched on feed refusal; the runner never force-flats or overwrites them.
- `ForwardPaperSessionService`, the registered cash-account path, now requires `require_feed_health`, a preflight snapshot, and its observation time before running. It records that snapshot durably before preflight; retries reuse the identical snapshot, while stale snapshots fail closed before strategy, risk, or execution callbacks.

## Verification

`tests/test_forward_session_runner.py`, `tests/test_forward_session_crash_matrix.py`, `tests/test_forward_feed_health.py`, and `tests/test_forward_paper_account_bridge.py`: 80 passed. Ruff and mypy passed. Tests verify stale and missing health, service-level non-bypass, decision-clock failure, execution-clock uncertainty, durable incomplete state, and absence of paper intent/clean close on pre-entry outage.

## Remaining evidence gap

The direct `ForwardSessionRunner` still exposes `require_feed_health` as a caller policy, and the Kite poller is not yet connected to the registered account service to enforce health for every observed paper session. Continuous intra-session monitoring is not wired to a production session orchestrator, and no real forward outage drill is claimed. Therefore this task remains PARTIAL.
