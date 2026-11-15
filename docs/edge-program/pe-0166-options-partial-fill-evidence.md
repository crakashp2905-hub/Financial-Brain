# PE-0166 — Multi-leg partial fills and legging exposure

## Acceptance

Paper option spread performance retains partial and unmatched leg fills, pending lots,
unfilled orders, explicit sourced margin, and conservative closeout evidence. A missing,
stale, invalid, or depth-limited close quote leaves the affected position unresolved.
Expiry stress scenarios show the P&L effect of partial legging and include entry fees.

## Implementation and verification

`financial_brain.paper.options_spread_lifecycle.evaluate_spread_lifecycle` accepts a
frozen vertical plan, source-hashed fills, contract and margin evidence, and close quotes.
It reports filled and unfilled lots, unmatched long/short exposure, defined-spread and
naked-short margin reserve, depth-limited closeout P&L, and caller-specified expiry spot
stress results. It never converts unfilled quantity to a position or treats a missing
close quote as a flat close.

Six focused tests cover paired partial fills, naked short legging, no-fill retention,
partial/stale closeout, duplicate/future/overfilled events, and contract-master mismatch.
Together with the PE-0165 option evidence suites, 17 tests pass; Ruff passes. Source
inventory verification passes for 614 Python files.

## Limits

Scenario P&L is net of entry fees only and depends on the caller's stress prices. It does
not model physical assignment or settlement charges; PE-0167 handles that lifecycle.
Margin is an explicit source-hashed input, not a computed NSE SPAN result. Synthetic tests
do not establish live fills or profitability.

## Status

DONE for the defined partial-fill, margin, closeout and stress acceptance. Exchange margin
and contract settlement evidence remain external inputs.
