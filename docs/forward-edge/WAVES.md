# Forward-edge program: wave changesets (F00, F01)

Branch: `feature/forward-edge-f00-f01` (based on the tree of `origin/main` at `6321153`; the feature tip `7e21674`
has identical content). Nothing was pushed. Physical orders remain disabled everywhere (`PHYSICAL_ORDERS_ENABLED = False`).

## Wave F00 - baseline, evidence firewall, deployment safety

| Item | Change | Evidence |
|---|---|---|
| FE-0001 | `forward_edge/baseline.py`; `docs/forward-edge/baseline-2026-11-10.json` | `test_baseline_*` |
| FE-0002 | `forward_edge/trials.py`; `docs/forward-edge/protected-pins.json` (ledger + manifests, cross-checked against `docs/audits/protected-baseline.json`). Ledger not modified. | `test_*trial*`, `test_protected_records_match_pinned_digests`, `test_retrospective_edit_*` |
| FE-0004 | `forward_edge/charter.py` | `test_charter_*`, `test_revision_*`, `test_malformed_charters_*` |
| FE-0005/0006 | `forward_edge/modes.py` | `test_live_cash_*`, `test_flipping_*`, `test_only_live_paper_*` |
| FE-0008 | `forward_edge/approvals.py` | `test_software_actor_*`, `test_non_human_*`, `test_expired_*`, `test_wrong_kind_*` |
| FE-0010 | `forward_edge/audit.py` (`python -m financial_brain.forward_edge.audit`) | `test_audit_*` |
| FE-0047..0053 (partial) | `forward_edge/custody.py` (Ed25519 pinned-root verifier, five terminal statuses, LOCAL_ONLY fallback) | `test_*receipt*`, `test_self_signed_*`, `test_forged_*`, `test_altered_*`, `test_false_clock_*`, `test_late_*` |
| FE-0148 | not runnable here: Linux blocked | see BLOCKERS.md |
| FE-0007 | blocked: `main` unprotected | see BLOCKERS.md |

Not started in F00: FE-0003 (belongs to F02 registry), FE-0009 (disposable DuckDB harness).

## Wave F01 - transactional correctness

| Item | Change |
|---|---|
| FE-0011 | `kernel/events.py`: a side effect is marked dispatched only after success; a raised action becomes UNKNOWN |
| FE-0013 | `kernel/events.py`: `resolve_unknown` requires a named reconciler; UNKNOWN is never re-run |
| FE-0014 | `kernel/oms.py`: `record_fill` validates everything before mutating anything |
| FE-0015/0016 | `kernel/oms.py`: settled cash moves with fills; partial fills release only their share of the reservation |
| FE-0017 | `kernel/pilot.py`: a repeated entry returns `DUPLICATE_SUPPRESSED` with no ledger posting; OMS cash is reconciled to the ledger on every run (`oms_reconciled`) |
| FE-0019 | `kernel/oms.py`: `provider_fill_id` makes postbacks idempotent; stale ACKs refused; FILLED refused while quantity is open |

Existing tests whose expectations encoded the old behaviour were corrected, not deleted:
- `tests/test_kernel_oms.py::test_full_lifecycle_releases_reservation` - available cash now falls by the spent notional (50,160, was 100,000).
- `tests/test_kernel_oms.py::test_kill_all_cancels_working_orders_and_keeps_fills` - 95,000 after 10 shares bought at 500 (was 100,000).
- `tests/test_kernel_pilot.py::test_resume_does_not_resubmit_the_entry` - the repeat run is `DUPLICATE_SUPPRESSED` with an empty ledger (was a second full trade).

Not done in F01: FE-0012 (durable WAL), FE-0018 remainder (dividends, splits, futures, options), FE-0020/0021 (power-loss and restore tests need the durable journal).

## Tests

- New: `tests/test_forward_edge_f00.py`, `tests/test_forward_edge_f01.py`, `tests/test_forward_edge_tracker.py`.
- Logs: `docs/forward-edge/logs/` (see the file names there).
- Platform: Windows 11, Python 3.13.5, pytest 9.0.3. Linux: not run (no WSL installed; the CI workflow has not been triggered).
