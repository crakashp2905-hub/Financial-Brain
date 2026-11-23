# Clean release candidate audit — 2026-11-23

Checklist item: **PA-01-15** — “Require one clean release candidate from a fresh clone with no developer-specific data paths; report missing external prerequisites without hiding failed test coverage.”

## Implemented controls

- Every GitHub Actions job starts with `actions/checkout`; the test matrix installs the exact locked environment and runs the full pytest suite. JUnit results are uploaded with `if: always()` and the step summary reports tests, skips, failures and errors.
- `scripts/release_candidate_audit.py` scans runtime source, scripts, workflows and dependency configuration for developer-specific absolute paths; parses the JUnit file and reports missing, invalid or failed test evidence; and reports non-green profit-readiness gates plus source/use data-rights status counts.
- The audit has a distinct `engineering_candidate` result and `release_candidate` disposition. Open performance, capital or rights prerequisites remain visible as blockers; they do not erase test failures or make the engineering check fail by themselves.
- The portability scan found `D:\Finance-Brain\scripts\paper_intraday.cmd` in the Task Scheduler wrapper usage comment. `scripts/run_hidden.vbs` now uses a `<checkout>` placeholder; a synthetic JUnit run over the current repository reports zero developer-specific path findings.
- Focused tests verify path rejection, preserved JUnit failure/skip counts, missing-report failure, and open external-rights visibility.

## Remaining acceptance evidence

The current source has not yet completed a fresh hosted Ubuntu/Windows run with downloadable `release-candidate-audit` artifacts at the final pushed SHA. The local clean-path audit is not a substitute for that fresh-checkout run.

**Status: PARTIAL.** Hosted acceptance requires both platform audit artifacts to report `engineering_candidate=PASS`, include exact test counts with zero failures/errors, report the known 102 unresolved market-data permissions and open profitability/capital gates, and bind to the same source SHA as the CI run.
