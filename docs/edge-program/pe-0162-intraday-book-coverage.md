# PE-0162 — Intraday book and spread coverage gate

## Acceptance

Before a sub-30-minute executable-alpha study can proceed, every predeclared candidate
interval must have a timestamped, observable, two-sided quote with positive displayed bid
and ask size, source and exchange provenance, and a spread within the configured limit.
The newest quote available by the interval execution time is used. A newer unusable quote
cannot be bypassed by falling back to an older book. Missing, stale, one-sided, LTP-only,
wide, halted, auction, or provenance-free intervals block the complete cohort; they are
not removed from the denominator and no spread is imputed.

## Implementation and verification

`financial_brain.evaluation.intraday_book_coverage.assess_sub30_executable_coverage`
implements the fail-closed gate and emits deterministic per-interval evidence hashes.
The report has no alpha field: callers may evaluate executable alpha only when
`allow_executable_alpha` is true, then must calculate costs from the observed quote
evidence. Requests at or above 30 minutes and empty candidate schedules are rejected.

Six focused tests cover LTP-only rejection, complete quote/depth eligibility, missing
source provenance, wide spreads, invalid requests, and deterministic evidence hashes.
Ruff passes for the module and tests. These tests use synthetic fixtures; they establish
the gate's behavior, not live Kite coverage, actual fill quality, or profitability. A
prospective coverage report still requires authorized captured market data.

## Status

DONE for the software acceptance criterion. Actual data coverage and sub-30-minute fill
economics remain unmeasured until prospective quote/depth captures exist.
