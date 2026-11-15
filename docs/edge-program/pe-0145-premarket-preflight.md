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

The repository does not yet obtain all probes from authoritative calendar,
entitlement, broker-session, cost, and clock services, nor
does the daily launcher invoke this new preflight automatically. Inputs remain
injected and must be wired to authorized provider probes before an operational
session. No real session readiness is claimed.
