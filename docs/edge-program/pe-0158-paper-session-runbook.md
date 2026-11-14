# PE-0158 — Paper session operator runbook

## Implemented

- Added a self-contained operator runbook for premarket preparation, intraday observation and order monitoring, end-of-session reconciliation, and exception handling, with exchange dates and operator display times explicitly scoped to `Asia/Kolkata`.
- The runbook names the required predeclared book/cohort/strategy/risk/cost inputs; covers calendar, credentials, disk, feed, data-contract, risk and system-health gates; and specifies fail-closed treatment for stale feed, unknown order state, partial fill, missing mark, restart, and missing evidence.
- Commands, arguments, exit codes, archive schema, restart identity and expected report limitations are documented. It clearly distinguishes fixture/archived replay from observed Kite paper and identifies components that remain unconnected to a daily launcher.

## Verification

Executed the documented fixture command sequence from the repository root with `PYTHONPATH=src`: preflight `0`, failed-feed rehearsal `2`, fixture paper run `0`, fixture close `0`, and fixture report `4` (`INSUFFICIENT_EVIDENCE`). Added a regression test for those exact outputs and safety labels. `tests/test_paper_session_runbook.py`, `tests/test_forward_edge_f13.py`, and `tests/test_forward_offline_paper_cli.py`: 45 passed; Ruff passed.

## Operational boundary

The runbook satisfies the documented offline walkthrough requirement. It does not claim that the daily observed-Kite launcher, paper-book/OMS bridge, EOD source integration, or prospective session evidence is complete; those are tracked separately in the checklist.
