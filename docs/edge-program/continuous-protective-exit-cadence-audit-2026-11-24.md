# Protective-exit cadence audit — 2026-11-24

Checklist item: `PA-03-02` — evaluate protective exits on incoming market updates independently of slower forecasting bars.

## Implemented and verified

- `SessionLoopHandlers.on_protective_update` is dispatched for each tick, quote, depth, or halt event as it arrives. A completed bar also receives a protective update before the strategy's completed-bar callback.
- Incomplete and unmarked bars are not dispatched to either callback. Strategy forecasts therefore run only at explicitly completed bars, while quote/tick monitoring does not wait for a 15-minute bar close.
- Added a test with three quote/tick updates between the session start and a completed 15-minute bar. It asserts every update reaches protective monitoring before the bar strategy callback.
- Verification after the cadence change: session-event-loop and market-event suites — 25 passed; scoped Ruff and mypy passed. Before the cadence-specific case, the event-loop, market-event, and durable session-runner suites passed 87 tests.

## Remaining limits

Status remains `PARTIAL`. The loop now exposes separate cadences, but a production protective-exit evaluator and its connection to the durable exit-protection ledger and paper execution are still injected responsibilities. The tests prove dispatch cadence, not exit-policy correctness or fill response latency.
