# Branch protection, required checks and CODEOWNERS audit

## Last successful GitHub settings read

`docs/edge-program/main-branch-protection.v1.md` records an API read-back on 2026-11-21. At that time `main` required PRs, required strict/up-to-date status checks `test (ubuntu-24.04)`, `test (windows-latest)`, `reproducibility (ubuntu-24.04)`, and `reproducibility (windows-2022)`, and had **zero required approvals**, as requested by the owner. Code-owner review and approval after the latest push were disabled; protection applied to administrators.

## Current evidence and limitation

- A fresh read on 2026-11-23 of `GET /repos/crakashp2905-hub/Financial-Brain/branches/main/protection` returned GitHub API **403 Resource not accessible by integration**. This connector cannot confirm whether the settings still match the 2026-11-21 read-back.
- `.github/workflows/ci.yml` still defines the exact four context names captured above. That only proves workflow job names, not that GitHub currently requires them.
- `scripts/validate_codeowners_map.py` passes; `.github/CODEOWNERS` covers the documented domains. CODEOWNERS routing does not itself require a review, consistent with the owner's zero-approval preference.
- PR #87's hosted checks are commit-specific and pending; no local validator is represented as a GitHub required check.

**Checklist result: PARTIAL.** The last known configuration honors the owner's approval preference and required checks. Current GitHub enforcement cannot be reverified without an integration credential with branch-protection administration read access. No approval requirement was reintroduced.
