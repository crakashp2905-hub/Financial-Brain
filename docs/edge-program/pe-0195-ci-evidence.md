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

PE-0195 remains **PARTIAL** until the exact pushed revision passes Ubuntu and Windows CI and PR #32 is merged to `main`. The prior failing run and the in-flight run predate this line-ending fix. PR #32 has no independent approving review; branch protection remains in force. No self-approval or bypass is performed.
