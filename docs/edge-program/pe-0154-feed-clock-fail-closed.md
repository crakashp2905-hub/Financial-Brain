# PE-0154 — Feed and clock failure closeout

## Implemented

- `ForwardRunConfig.require_feed_health` makes feed-health evidence mandatory for an observed paper session while preserving historical replay configuration identity when the flag is false.
- Required but absent feed health, stale quotes/heartbeats, gaps, disconnections, dropped updates, future timestamps, and other unsafe feed status block the strategy/risk/execution callbacks and produce a durable `SESSION_INCOMPLETE` record with the health evidence and reason codes. No `SESSION_CLOSED`, P&L success, or zero-loss completion is written.
- The session plan is now persisted before the decision clock is read. A naive/untrusted decision clock creates durable incomplete evidence and stops before decision or order creation.
- A clock failure after an order intent exists leaves the outbox explicitly UNKNOWN and without execution acknowledgment or session close; the system requires reconciliation instead of claiming no loss.
- Existing account, position, and risk records remain untouched on feed refusal; the runner never force-flats or overwrites them.

## Verification

`tests/test_forward_session_runner.py` and `tests/test_forward_session_crash_matrix.py`: 61 passed; Ruff passed. Tests verify stale and missing health, decision-clock failure, execution-clock uncertainty, durable incomplete state, and absence of paper intent/clean close on pre-entry outage.

## Remaining evidence gap

Callers must opt into `require_feed_health`; the Kite poller is not yet connected to enforce this for every observed paper session. Continuous intra-session monitoring is not yet wired to a production session orchestrator, and no real forward outage drill is claimed. Therefore this task remains PARTIAL.
