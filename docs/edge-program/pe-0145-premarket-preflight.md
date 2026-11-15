# PE-0145: Fail-closed premarket preflight

**Status: PARTIAL.** Added a premarket evidence record for exchange calendar,
market-data entitlement, read-only broker session, disk headroom, writable
archive, effective-dated cost schedule, frozen strategy identity, approved risk
configuration, and clock skew. Each requirement emits a named check and
operator-readable reason. Any missing, stale, invalid, or out-of-policy
requirement produces `NO_TRADE`, `approved: false`, and reason codes in a JSON
record accepted by `ForwardSessionRunner.record_preflight`.

`probe_local_archive_storage()` performs a real local disk-availability check and
write/fsync/delete probe for an existing archive directory. Its output contains
evidence IDs and a digest that can populate the snapshot's disk/archive fields.
The result is explicitly scoped to the local filesystem; it does not prove durable
production storage, data rights, or external service availability.

Validation: `python -m pytest -q tests/test_premarket_preflight.py` (21 passed)
and Ruff passed. Tests cover each mandatory input, the runner-compatible result
shape, stale cost schedules, malformed hashes, and simultaneous blockers.

`ForwardPaperSessionService` now accepts a frozen premarket policy and a
session-bound snapshot provider. The provider's session date/ID, registered
strategy ID/version/code digest, risk-policy digest, account fee-schedule digest
and effective date, and observation time are checked before the immutable
preflight result is journaled. The old generic callback is only an additional
veto: it cannot approve a session when the premarket provider is absent or its
evidence is invalid. Missing configuration therefore records `NO_TRADE`.
Synthetic integration tests verify the successful wiring and risk-policy
mismatch rejection.

The repository does not yet obtain all probes from authoritative calendar,
entitlement, broker-session, cost, and clock services, nor
does the daily launcher invoke this new preflight automatically. The provider
remains caller-configured and its evidence must be sourced from authorized
services before an operational session. No real session readiness is claimed.
