# PE-0163 — Point-in-time regime features

## Acceptance

Regime features used for a decision must have both event time and source-availability time
at or before the decision. For a morning cutoff, same-day full-session observations are
ignored. If core as-of inputs are unavailable, the result is blocked and carries no regime
label. The snapshot pins the existing regime classifier version and hashes the selected
inputs.

## Implementation and verification

`financial_brain.regime.asof.classify_asof` selects the latest eligible observation for
each classifier input and excludes future-event and late-arriving records. It returns a
fail-closed status when the cutoff lacks core price and moving-average features. Existing
daily strategy logic separately uses the prior completed regime; a same-day regime cannot
approve its own breakout.

Five focused as-of tests cover valid prior data, full-day/future perturbations, late-arriving
data, missing morning inputs, and timestamp/hash validation. The existing script-mechanism
tests also pass (12 tests total across both modules); Ruff passes. Source inventory
verification passes for 612 Python files.

## Status

DONE for the point-in-time feature contract and regression acceptance. The as-of API is a
research/data contract; this package does not claim that a live intraday regime feed has
been captured or used in profitable decisions.
