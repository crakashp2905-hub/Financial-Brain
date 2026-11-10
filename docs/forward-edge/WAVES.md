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

## Waves F02 .. F13 (all offline; nothing pushed until the F13-tip suite was green)

Branches are stacked: each wave branch starts at the previous wave's tip. Targeted tests are the wave's own file; the full
Windows suites are listed under "Suite results".

| Wave | Branch tip | New module | Targeted tests |
|---|---|---|---|
| F02 registry | `eddd06e` | `forward_edge/registry.py` | 24 |
| F03 market data | `b46ae47` | `forward_edge/market_data.py` | 21 |
| F04 custody | `d637e11` (+ pin fix `36c2c78`) | `forward_edge/decision_custody.py` | 10 (+1 regression) |
| F05 execution | `46098d9` | `forward_edge/execution.py` | 20 |
| F06 session ops | `5af3351` | `forward_edge/session_ops.py` | 12 |
| F07 research tools | `8675248` | `forward_edge/research_tools.py` | 12 |
| F08 statistics | `c170c30` | `forward_edge/evidence_stats.py` | 21 |
| F09 portfolio risk | `04af8dc` | `forward_edge/portfolio_risk.py` | 15 |
| F10 cockpit | `0ee7788` | `forward_edge/cockpit.py` | 16 |
| F11 governance | `7a3adaa` | `forward_edge/governance.py` | 11 |
| F12 rehearsal | `d3c7dde` | `forward_edge/rehearsal.py` | 22 |
| F13 release | `2762b33` | `pipeline.py`, `cli.py`, `fixtures.py`, `release.py` | 38 |

### Defect found and fixed during F04 verification

The first full-suite run on the F04 tip failed two F00 tests. Cause: `docs/trials/ledger.jsonl` has no `eol` attribute, so a fresh
Windows checkout (autocrlf) rewrites it to CRLF and the raw-byte pin no longer matched. A Linux checkout would have failed the
same way. The pin now hashes LF-normalized content (equal to the git blob digest) and a regression test covers it. The
ledger itself was never modified.

### Suite results (Windows 11, Python 3.13.5, pytest 9.0.3)

| Tip | Result | Log |
|---|---|---|
| F00 + F01 (merged `7cb826b`) | 2979 passed, 1 skipped | `logs/full-suite-windows-rerun.txt` (earlier run timed out at 96%: `full-suite-windows-20261110-1234.txt`) |
| F04 first run | 2 failed (ledger pin, see above), 3032 passed | `logs/full-suite-f04-tip.txt` |
| F04 after fix | 3035 passed, 1 skipped | `logs/full-suite-f04-fixed.txt` |
| F13 tip | see `logs/full-suite-f13-tip.txt` | |

Linux was not run: WSL is not installed here. Golden session signatures are committed in
`tests/fixtures/forward_edge/golden_signatures.json` so a Linux run can prove byte-identical output.

## Wave F14 - durability and the remaining offline items (branch `feature/forward-edge-f14`)

| Item | Change |
|---|---|
| FE-0011/0012/0013 | `forward_edge/durable.py`: fsynced, monotonic, hash-chained journal; PENDING written before the external action; any non-ACKED key is reconciled against the venue after a restart, never blindly resent |
| FE-0020 | 25 injected power-loss cases (8 stages x before/torn/after, plus send-effect-only): same state hash as an uninterrupted run, exactly one send, exactly one fill |
| FE-0021 | atomic rollover (temp file + replace), backup and restore reproduce the state hash |
| FE-0072 | durable decision journal with replay-identical head hash |
| FE-0009 | `forward_edge/disposable.py`: scratch DuckDB in a temp directory; real data paths refused |
| FE-0003 | `forward_edge/eligibility.py` and `docs/forward-edge/strategy-eligibility.json` (13 failed trials, 12 strategy modules shadow-only) |
| FE-0028 | `forward_edge/books.py`: one OMS per strategy book, no implicit transfers |
| FE-0063 | `forward_edge/futures.py`: roll, basis, lot-size residual, variation margin, financing |

A defect was found by the tests during this wave: journal recovery first treated a newline-terminated but invalid final
record as a torn write and discarded it. That would have hidden tampering. Only an unterminated tail is now treated as a
power-loss artefact; any other invalid record raises `JournalCorrupt`.

Tracker after this wave: DONE 75, PARTIAL 65, BLOCKED 12, NOT_STARTED 0. Forward-edge tests: 350 passed.
The legacy in-memory `EventKernel` and the kernel pilot are not rewired to the durable journal.
