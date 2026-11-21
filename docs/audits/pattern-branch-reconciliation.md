# Chart patterns: branch reconciliation (CP-0002, CP-0006)

Recorded from local git state. The remote was not contacted, so no PR state is claimed.

- Base branch: `main` at `a2130ba` (Add provider-neutral evidence anchor contract, #15).
- Work branch: `codex/source-notes-pattern-books`, five commits ahead of `main` before wave 9:
  - `713a402` chart-pattern wave 1
  - `7598c3c` waves 2 and 3
  - `11b0a22` wave 4
  - `b4fe829` waves 4 to 8
  - wave 9, recorded in the completion matrix
- Feature isolation: the chart-pattern work lives on its own branch, not on `main`. Other `codex/*` branches in the repository are unrelated work and were not read or merged.
- Research PR status: not checked against GitHub in this step. Whether the source-notes PR is merged or superseded needs a remote lookup, which is outstanding.
- Local tag: `patterns-engineering-wave-8` at `b4fe829`.

Nothing has been pushed. Pushing requires an explicit instruction.
