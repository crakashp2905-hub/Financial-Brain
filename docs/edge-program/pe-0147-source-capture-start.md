# PE-0147: Source capture start receipt

**Status: PARTIAL.** Added an immutable source-capture start receipt for the
forward-session journal. It records capture ID and start time, earliest decision
clock and lead time, clock skew and evidence ID, initial quote-health counters
and reasons, and source entitlement records with validity windows. Capture must
precede the earliest decision, and this marker must be persisted before any
market event. Repeated identical receipts are idempotent; changed receipts or
late markers are rejected. Missing or invalid entitlements are explicitly
reported as blocking.

`forward_events()` now accepts the capture runner, frozen run configuration,
and typed receipt together. It persists the marker before making the Kite quote
request, so the session journal orders the capture receipt before normalized
market events. The regular `run_live()` polling loop now accepts the same
context, persists it once before its first live quote request, and routes later
polls into the existing forward-event sink. When an event sink is enabled but
the receipt is absent, invalid, for another session date, or lacks a valid
entitlement, forward events are suppressed and its preflight is forced to
`NO_TRADE`.

Validation: `python -m pytest tests/test_phase5_intraday_runner.py
tests/test_source_capture_start.py tests/test_kite_forward_events.py -q` (40
passed), Ruff, and mypy passed. Tests verify that the live runner journals the
receipt before the first Kite quote request and before normalized market
events, and that missing receipts suppress event emission and block preflight.
Other coverage includes pre-decision timing, entitlement expiry/missing
evidence, initial no-quote health, clock evidence, and immutable replay.

The caller still supplies the capture receipt; no authenticated entitlement
provider or trusted clock service is connected. No actual source-capture session
has been observed. Those external integrations remain required before claiming
operational source capture.
