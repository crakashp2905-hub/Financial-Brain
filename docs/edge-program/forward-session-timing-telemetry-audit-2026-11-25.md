# Forward paper-session timing telemetry audit

Date: 2026-11-25

`ForwardSessionRunner.records(session_id)` now publishes
`fb.forward_session_timing_telemetry.v1`, joining each decision to its exact input
event source/exchange/receipt/availability/ingest clocks, explicit feature-ready clock,
signal decision clock, risk approval, order-state times and simulated partial/full fill
times. Missing clocks and lifecycle stages are listed per signal rather than filled from
unrelated timestamps. `feature_ready_at` is an optional decision input; when supplied it
must be timezone-aware, no earlier than every input event's availability and no later than
the decision.

Focused acceptance: the timing-record projection and feature-ready ordering tests plus the
existing durable candidate-to-fill test report 3 passed. Ruff and mypy pass for the runner.

The report is **partial evidence**. Production strategy adapters do not yet provide the
feature-ready clock consistently; source/exchange clocks can be absent; stop-trigger and
exit-role/execution attribution are not yet durably recorded for every trade; no independent
captured-trace validation or cockpit latency/coverage display is established. The report
marks those gaps instead of inferring them.
