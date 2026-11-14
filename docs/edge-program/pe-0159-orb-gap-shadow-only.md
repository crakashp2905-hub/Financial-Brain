# PE-0159 — Keep evidence-failed ORB/GAP out of formal paper allocation

## Implemented

- The legacy observed-quote intraday runner now stores all new ORB and GAP simulator outcomes under the `shadow` mode. The evidence classifier labels these rows `EXPLORATORY_SHADOW`; daily book/champion consumers reading the `live` local-paper mode cannot consume the new rows as formal allocation evidence.
- Other explicitly selected custom strategies retain their existing mode mapping. Historical ORB/GAP `live` rows are not migrated, deleted, or overwritten and remain available to existing query/report paths.
- The 09:30 board and live paper viewer state the SHADOW-only policy, distinguish observation from allocation, and avoid presenting evidence-failed setups as new champion-book trades.

## Verification

`tests/test_phase5_intraday_runner.py`, `tests/test_paper_live_view.py`, `tests/test_paper_capabilities.py`, and `tests/test_monitoring_candidates.py`: 30 passed; Ruff passed. Tests verify a newly observed ORB outcome is stored as `shadow`, no new ORB/GAP rows enter `live`, a seeded historical failed ORB `live` outcome stays queryable, and the report/view label the shadow boundary.

## Boundary

This changes the existing intraday runner's evidence lane; it does not establish that ORB or GAP has positive expectancy. Shadow simulator P&L remains exploratory and is excluded from the formal champion paper book. No live broker orders are introduced.
