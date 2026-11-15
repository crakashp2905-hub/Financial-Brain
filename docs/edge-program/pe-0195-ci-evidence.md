# PE-0195 — Ubuntu and Windows CI evidence

## Current CI diagnosis

The last PR check for the checklist branch passed the full test suite but failed the capability-evidence manifest audit on Ubuntu. The manifest generator hashed raw source bytes. Windows worktrees expose CRLF while Git text blobs and Ubuntu worktrees use LF, so identical tracked source produced different SHA-256 values.

## Fix and local verification

- `scripts/audit_capability_evidence.py` now canonicalizes CRLF and CR to LF before hashing source and test files.
- `tests/test_capability_evidence_audit.py::test_manifest_hash_is_stable_across_windows_and_unix_line_endings` verifies equal manifests for LF and CRLF worktrees.
- Focused audit test suite: **3 passed**; Ruff passed.
- Full local suite before this focused hash fix: **4,080 passed, 1 skipped**. Repository checks and `--check` passed locally.
- Capability manifest regenerated with SHA-256 `c1fad098a9638413b635e911e084ccfb4288fb66d136b81ede6ab3a27b2d693c`.

## Current local checkout verification

At local commit `4bbf251`, the full Windows test suite passed: **4,143 passed, 1 skipped** (the opt-in MCP protocol test). Ruff, capability-matrix validation, source-inventory freshness, mypy, migrations, algorithm and pattern matrices, offline pattern demo, critical-domain coverage floors, the pattern coverage floor, and the refreshed capability-evidence audit passed. The local `pip-audit` command could not run because `pip_audit` is not installed in the active Python environment. These results do not establish a clean wheelhouse install or Linux CI for `4bbf251`.

Those local results are historical and do not describe the current PR head. PR #32 is open at `07c30e714b4ae256ee514f33a359b6db5e79f117`; the checklist changes are pushed. CI run `43676361975` was still running on parent `1126d36` when this record was refreshed, and the new head's CI had not yet appeared. The PR has no approving review recorded, so it cannot merge under the configured branch-protection rule.

## Remaining acceptance

The line-ending fix, task-evidence hash checks, capability-manifest audit, and full suite passed on both platforms in [CI run #43617026883](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43617026883) (push) and [CI run #43617031014](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43617031014) (pull request) at commit `c2c1603e9c2fa57c077f22f38fd8dbe709153740`. Those runs validate that earlier revision only; current-head CI and independent review remain required.

PE-0195 remains **PARTIAL** because PR #32 is not merged to `main` and has no independent approving review. Branch protection remains in force. No self-approval or bypass is performed.
