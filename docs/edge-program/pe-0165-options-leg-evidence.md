# PE-0165 — Timestamped multi-leg option evidence

## Acceptance

Every option leg must have a fresh two-sided bid/ask quote available by the decision time.
Each quote is bound to a source-hashed contract-master row for venue, expiry and lot size.
The planned venue must match the quote and master venue for every leg. A missing, late,
stale, one-sided, invalid, or contract-mismatched leg blocks the complete paper spread.

## Implementation and verification

`financial_brain.paper.options_spread.prepare_debit_vertical` is the fail-closed readiness
gate around the existing spread risk and cost calculator. It checks each leg's observed
and available timestamps, quote age, bid/ask and displayed size, quote and contract-master
hashes, venue identity, expiry and lot compatibility. It returns spread economics only
when all leg evidence passes. The lower-level `debit_vertical` remains a calculator and
does not itself certify timestamp or venue evidence.

Six new acceptance cases plus the existing spread tests pass (11 total); Ruff passes.
Fixtures are synthetic. This is paper-only and does not represent a broker order route or
observed live option fill.

## Status

DONE for the multi-leg paper readiness contract. Live quote retention, venue permissions,
and prospective option fill results still require authorized source evidence.
