# Main branch protection evidence

Verified through the GitHub REST branch-protection endpoint on 2026-11-21 at
12:00 UTC (`GET /repos/crakashp2905-hub/Financial-Brain/branches/main/protection`).

- `main` requires changes to arrive through a pull request.
- Required status checks are strict: the PR branch must be up to date with `main`.
- Required checks are `test (ubuntu-24.04)`, `test (windows-latest)`,
  `reproducibility (ubuntu-24.04)`, and `reproducibility (windows-2022)`.
- Required approving reviews: **0**, per the owner's instruction to remove the
  approval requirement. Code-owner reviews and last-push approval are disabled.
- Protection is enforced for administrators.

The settings were read back from GitHub after the update. This verifies repository
configuration only; it does not assert that a particular PR's checks have passed.
