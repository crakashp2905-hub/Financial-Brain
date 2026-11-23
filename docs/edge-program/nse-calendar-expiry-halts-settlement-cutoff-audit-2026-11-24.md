# NSE calendar, expiry, halt, settlement, and cutoff audit — 2026-11-24

Checklist item: `PA-02-14` — use an aware exchange calendar, effective-dated expiry/settlement data, session halts/special sessions, and instrument-specific cutoffs.

## Existing support and change

- `forward_edge.venue_calendar.VenueCalendar` requires source-referenced, SHA-256-bound NSE/BSE sessions, enforces a source horizon, and converts local exchange session times to UTC using Asia/Kolkata. Missing dates do not become guessed weekdays. `ForwardSessionRunner.plan_calendar_day` records exact bounds and requires explicit allowlisting for non-`NORMAL` session types.
- `forward_edge.instrument_master.InstrumentRecord` carries effective date ranges and exact listed future/option expiry; `resolve_option` refuses expired or absent contracts. `InstrumentRuleBook` resolves dated status, expiry, tick, lot, freeze quantity, and allowed phases.
- The event model now accepts only `HALTED` or `RESUMED` states (also reading the legacy `status` key). The paper executor derives halt state using events available by simulated execution time and refuses a fill while the instrument remains halted. A later available resume restores fill eligibility.
- No external calendar, expiry, settlement, or halt source is authenticated by these code contracts.

## Verification

- `python -m pytest tests/test_forward_edge_session_runner.py tests/test_forward_edge_market_events.py -q`: 81 passed, including a paper fill blocked during an available halt and accepted after an available resume.
- Scoped Ruff, mypy, and `git diff --check` passed.
- Forward-engine graph, source inventory, capability ownership, and entry-point inventory checks passed after source changes.

## Remaining limits

Status is `PARTIAL`. The repository has no effective-dated, source-backed settlement schedule linking each contract to settlement date, method, and delivery/cash treatment. Instrument-specific last-entry/exit cutoffs are not represented; only venue session bounds and phase eligibility exist. Halt enforcement is in the canonical paper fill path and is not yet a general decision gate across all algorithm adapters. Production data completeness and source rights for the calendar/contract lifecycle are unverified.
