# PE-0190 — Profitability claims with uncertainty and context

## Acceptance

Display confidence intervals, multiplicity count/correction, matched benchmark, and estimated capacity beside every profitability claim. Win rate or positive total rupees alone must not produce a “proven” label.

## Implementation and verification

`forward_edge.cockpit.profitability_evidence` renders a row for each claim with confidence level and bounds, net-alpha lower bound, number of tested candidates, familywise-adjusted alpha and adjustment flag, matched benchmark and outcome, estimated capacity, and descriptive win-rate/rupee totals. Missing, invalid, or non-positive required evidence stays `INSUFFICIENT / NON-POSITIVE EVIDENCE`. A positive adjusted lower bound is labeled as uncertain and explicitly “NOT A GUARANTEE”; the view never uses “PROVEN.”

Verification: `tests/test_profitability_evidence_view.py` — 3 passed; combined profitability/cockpit run — 23 passed. Ruff and targeted mypy passed.

## Limits

The page presents caller-supplied inferential statistics; it does not compute confidence intervals, multiplicity correction, matched results, or capacity. Upstream procedures must establish those values and aligned evaluation periods. A positive bound is still conditional on those assumptions and evidence quality.
