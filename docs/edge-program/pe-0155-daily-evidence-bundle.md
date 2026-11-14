# PE-0155 — Portable daily evidence bundle and operator report

## Implemented

- Added a portable directory bundle containing the exact paper-account close archive, underlying account journal, and durable runner journal, plus any additional caller-selected evidence inputs. Each input path, byte count, and SHA-256 is listed in a canonical manifest.
- The operator report is generated only from the exported, hash-verified account archive and journal. It includes session outcome, NAV/cash, order quantities, inventory and marks, event denominators, and evidence hashes; it does not imply live execution or profitability.
- A different workstation can independently validate the EOD archive hash, runner journal hash chain, manifest and all input hashes, then regenerate and byte-compare the report. The bundle rejects undeclared files and symlinks.
- Optional Ed25519 signing is supported when a caller supplies an owner-controlled key. The verifier reports unsigned/hash-only, valid-but-unpinned, or trusted-key signature state; callers must pin the expected public-key hash to authenticate the signer.
- Bundle creation is idempotent for the same manifest and refuses altered content at an existing output path.
- `close_and_bundle_paper_session` now checks the durable journal chain, matching exchange date and terminal session record before closing the account. It then creates the EOD archive and portable bundle; retrying the same close inputs reuses the immutable archive and bundle. This is a callable orchestration primitive, not a scheduler.
- Bundle verification replays the copied account journal into the paper OMS/ledger, binds its exact sequence/head to the EOD close archive, and compares cash, available cash, orders, return reconciliation, and P&L. It also verifies positions, marks, and NAV. The account journal is mandatory; archive-only bundles are rejected.
- `fb forward daily-finalize` reconstructs the paper account from its journal and invokes close plus bundle creation in one command. It requires explicit observed close marks, capture time, runner journal, and terminal session evidence; it never creates physical orders.
- `python -m financial_brain.forward_edge.cli daily-paper-cycle --manifest <path>` now iterates every account in a frozen registry plan, checks dated terminal runner evidence, finalizes each account and bundle, and emits a by-book report from the resulting archives plus caller-supplied prior sessions.
- `daily-paper-preflight` validates the entire manifest before any close: account-journal replay and allocated cash, every book's marks and outstanding order states, and each terminal runner session's strategy/account binding. It is read-only and catches a later-book defect before an earlier book can be closed.
- `scripts/paper_daily_finalize.cmd` is a Windows Task Scheduler-ready wrapper. It requires an explicit operator-managed manifest path, runs preflight before finalization, logs both results, and propagates failure. It does not register or enable a scheduled task.
- `daily-paper-manifest` derives per-book session IDs and runner paths from verified journals, binds them to the selected frozen plan, and writes a content-hashed immutable manifest. It refuses missing or ambiguous strategy sessions; the owner still supplies close marks and prior history.
- The cycle derives account journal locations from the frozen registry and fails before finalization unless manifest membership exactly matches the plan. It is resumable per book through immutable close archives and bundles.
- [Daily cycle manifest and operator contract](../forward-edge/daily-paper-cycle.md) documents required inputs, scheduler invocation, and retry behavior.

## Verification

`tests/test_daily_evidence_bundle.py` and `tests/test_eod_paper_archive.py`: 14 passed; Ruff and mypy passed. The cross-workstation fixture copies the bundle, pins the Ed25519 key, verifies all artifacts, and reproduces the human report. Finalization tests cover idempotent CLI retry, account-journal replay and close reconciliation, journal tampering, and refusal before account mutation when there is no durable terminal session. Tampering and untrusted-key cases are refused.

## Remaining evidence gap

No Windows Task Scheduler job has been registered or enabled, and no process generates the operator manifest yet, so automatic completion after each actual paper session remains unverified. Production signing requires a managed owner key and out-of-band trusted-key pin. The package is a portable directory; operators must transfer it intact. This remains PARTIAL until scheduling and owner-controlled key custody are established.
