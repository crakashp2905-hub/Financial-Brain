# PE-0195 — Ubuntu and Windows CI evidence

## Current CI diagnosis

The last PR check for the checklist branch passed the full test suite but failed the capability-evidence manifest audit on Ubuntu. The manifest generator hashed raw source bytes. Windows worktrees expose CRLF while Git text blobs and Ubuntu worktrees use LF, so identical tracked source produced different SHA-256 values.

## Fix and local verification

- `scripts/audit_capability_evidence.py` now canonicalizes CRLF and CR to LF before hashing source and test files.
- `tests/test_capability_evidence_audit.py::test_manifest_hash_is_stable_across_windows_and_unix_line_endings` verifies equal manifests for LF and CRLF worktrees.
- Focused audit test suite: **3 passed**; Ruff passed.
- Full local suite before this focused hash fix: **4,080 passed, 1 skipped**. Repository checks and `--check` passed locally.
- Capability manifest regenerated with SHA-256 `c1fad098a9638413b635e911e084ccfb4288fb66d136b81ede6ab3a27b2d693c`.

## Remaining acceptance

The line-ending fix and capability-manifest audit passed in the complete cross-platform [CI run #598](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43601378017) at commit `2c61abe6cbc50e2efa864fb8c883a734431894ef`. Both platform jobs completed the full suite and post-suite evidence audit successfully.

PE-0195 remains **PARTIAL** because PR #32 is not merged to `main` and has no independent approving review. Branch protection remains in force. No self-approval or bypass is performed.
