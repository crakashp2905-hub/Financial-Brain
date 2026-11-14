# PE-0145: Fail-closed premarket preflight

**Status: PARTIAL.** Added a premarket evidence record for exchange calendar,
market-data entitlement, read-only broker session, disk headroom, writable
archive, effective-dated cost schedule, frozen strategy identity, approved risk
configuration, and clock skew. Each requirement emits a named check and
operator-readable reason. Any missing, stale, invalid, or out-of-policy
requirement produces `NO_TRADE`, `approved: false`, and reason codes in a JSON
record accepted by `ForwardSessionRunner.record_preflight`.

Validation: `python -m pytest -q tests/test_premarket_preflight.py` (19 passed)
and Ruff passed. Tests cover each mandatory input, the runner-compatible result
shape, stale cost schedules, malformed hashes, and simultaneous blockers.

The repository does not yet obtain all probes from authoritative calendar,
entitlement, broker-session, filesystem/archive, cost, and clock services, nor
does the daily launcher invoke this new preflight automatically. Inputs remain
injected and must be wired to authorized production probes before an operational
session. No real session readiness is claimed.
