# Deterministic forward-session loop audit — 2026-11-24

Checklist item: `PA-03-01` — one deterministic event loop for session open, completed bars, quotes, corporate actions, margin changes, decisions, protective exits, and closeout.

## Implemented and verified

- Added `forward_edge.session_event_loop.run_forward_session`, which plans/open-gates the session with the sourced venue calendar, validates session clocks, ingests each event through `ForwardSessionRunner`, dispatches handlers in deterministic availability order, and closes with a durable report.
- Same-availability ordering is explicit: corporate actions, margin changes, halts, market updates/protective monitoring, completed bars/strategy evaluation, then auxiliary events. Event IDs break remaining ties; duplicate IDs are rejected.
- Added the normalized `MARGIN_CHANGE` event contract with a stable identity and nonnegative INR before/after margin values.
- The loop journals each event before exposing it to a handler. Bars without an explicit completion flag are retained as evidence but never dispatched to strategy entry logic. Handler failures attempt an INCOMPLETE close and propagate the original error.
- Focused verification: `tests/test_forward_edge_session_event_loop.py`, `tests/test_forward_edge_market_events.py`, and `tests/test_forward_edge_session_runner.py` — 87 passed; scoped Ruff and mypy passed. The suite includes an integration path through the real durable runner for calendar open, event ingest, and close reporting.

## Remaining limits

Status remains `PARTIAL`. Strategy decisions, actual protective-exit policy, corporate-action ledger effects, margin accounting, and closeout behavior are explicit injected handlers, not yet bound to one standard production handler registry. Handler effects are not atomically coupled to the event journal and there is no durable dispatch offset/replay identity here; restart-safe offsets are a separate checklist requirement.
