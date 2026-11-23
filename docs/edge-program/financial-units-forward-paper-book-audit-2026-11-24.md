# Typed forward paper-book signal inputs

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

`PaperBookSignal` accepts `WholeUnits` for shares, INR `Price` values for stop/reference prices, and `BasisPoints` for expected-net and estimated-cost rates. It validates positive whole shares, INR price currency, finite rates, and nonnegative cost rates, then normalizes to the pre-existing integer/float bridge payload. The paper-book/runner serialized schemas remain unchanged.

## Evidence

- `src/financial_brain/forward_edge/paper_book_adapter.py::PaperBookSignal.__post_init__`
- `tests/test_forward_edge_session_runner.py::test_forward_paper_book_signal_accepts_typed_prices_units_and_rate_bps`
- `python -m pytest tests/test_forward_edge_session_runner.py -k 'forward_paper_book_signal_accepts_typed or paper_book_adapter_records_qualified_horizon' -q --basetemp=.pytest-checklist-forward-units-rerun` — 2 passed.
- Ruff and scoped mypy passed for the adapter and test module.

## Remaining scope

FB-0103 remains PARTIAL. The session runner's general OMS requests, fills, paper-accounting records, and risk-sizing result values still need nominal-unit integration and serialization/recovery verification.
