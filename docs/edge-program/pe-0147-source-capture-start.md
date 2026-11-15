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
market events. The arguments remain optional for compatibility; the deployed
launcher must supply them to use this path.

Validation: `python -m pytest -q tests/test_kite_forward_events.py
tests/test_source_capture_start.py` (19 passed), Ruff, and mypy passed. Tests
verify event ordering through the Kite adapter, pre-decision timing, entitlement
expiry/missing evidence, initial no-quote health, clock evidence, and immutable
replay.

The regular live Kite polling loop/launcher does not yet supply this API's
receipt arguments or produce authenticated entitlement and trusted clock
receipts. The receipt is caller-supplied, and no actual source-capture session
has been observed. Those integrations remain required before claiming
operational source capture.
