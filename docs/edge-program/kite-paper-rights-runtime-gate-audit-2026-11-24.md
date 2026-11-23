# Kite paper rights runtime gate — 2026-11-24

**Checklist:** `PA-02-02` (complete for the fail-closed live-paper gate); `PA-02-03` (partial).
**Permission posture:** no rights were granted or inferred.

The canonical `data-permissions.v1.json` records every KITE source/use permission as `UNKNOWN_RIGHTS`. The live intraday paper runner now loads that registry before any quote request and accepts an entitlement only when the caller-supplied row exactly matches the canonical source/use record and is effective for the current date. An arbitrary permission file cannot override the repository's unknown status. The normalized forward runner applies the same canonical comparison before accepting provider events. Synthetic test permissions require an isolated test registry.

The intraday ledger records the source, use, status, effective dates, approval evidence reference, reviewer and LF-normalized registry SHA-256 with the run's feed-health note. The normalized runner attaches the registry hash and permission row to each accepted provider event. Unknown, missing, mismatched or expired permissions stop the live quote request or event ingestion. No Kite live-quote paper session can start under the current checked-in registry.

Verification: focused tests for missing, forged, unknown and expired Kite permissions; accepted test-only fixture registry and permission evidence logging (6 focused tests passed across `test_phase5_intraday_runner.py` and `test_forward_edge_session_runner.py`). Scoped Ruff, source inventory, entry-point inventory, capability ownership, data-use matrix, checklist export, profit-readiness and `git diff --check` passed.

`PA-02-03` remains `PARTIAL`: the canonical registry and runtime enforcement cover the two Kite paper paths. The repository does not yet bind every non-Kite provider adapter to this registry or automatically stop every external datasource when its entitlement expires. Provider-specific written approval is still absent and all Kite permissions remain denied operationally.
