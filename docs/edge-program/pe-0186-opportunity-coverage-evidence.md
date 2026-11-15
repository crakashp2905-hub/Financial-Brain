# PE-0186 — Opportunity and session coverage

## Acceptance

Display exact rejected, abstained, missed, and no-signal counts with reason codes. Every planned session date remains in the coverage denominator.

## Implementation and verification

`forward_edge.cockpit.opportunity_coverage` accepts stable, unique event IDs and requires a reason code for each explicit outcome. It renders exact outcome counts and event-level reasons, refuses events outside the planned date set, and emits `UNRECORDED / NO_RECORDED_OUTCOME` for every planned date with no event. It never converts missing evidence to a no-signal observation.

Verification: `tests/test_opportunity_coverage_dashboard.py` — 4 passed; combined dashboard run — 24 passed. Ruff and targeted mypy passed.

## Limits

The caller must provide a complete governed schedule and all events. This renderer detects duplicate IDs and out-of-window records but cannot discover opportunities omitted before they reach it; the independent session/event capture path remains responsible for completeness.
