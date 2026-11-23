# Typed forward paper-book signal inputs

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

`PaperBookSignal` accepts `WholeUnits` for shares, INR `Price` values for stop/reference prices, and `BasisPoints` for expected-net and estimated-cost rates. It validates positive whole shares, INR price currency, finite rates, and nonnegative cost rates, then normalizes to the pre-existing integer/float bridge payload.

`ForwardSessionRunner.execute_paper_intent` also accepts `WholeUnits`, INR `Price` limit/tick/band values, and `BasisPoints` adverse slippage; it normalizes at the call boundary before existing risk, rulebook, fill, and accounting logic. Non-INR typed prices are refused. Persisted OMS and receipt fields remain their existing scalar representation.

## Evidence

- `src/financial_brain/forward_edge/paper_book_adapter.py::PaperBookSignal.__post_init__`
- `src/financial_brain/forward_edge/session_runner.py::ForwardSessionRunner.execute_paper_intent`
- `tests/test_forward_edge_session_runner.py::test_forward_paper_book_signal_accepts_typed_prices_units_and_rate_bps`
- `tests/test_forward_edge_session_runner.py::test_paper_book_adapter_records_qualified_horizon_and_abstains_on_uncalibrated_factor` exercises typed OMS inputs through the fill path.
- `python -m pytest tests/test_forward_edge_session_runner.py -k 'forward_paper_book_signal_accepts_typed or paper_book_adapter_records_qualified_horizon' -q --basetemp=.pytest-checklist-forward-units-rerun` — 2 passed.
- Focused typed OMS integration: `python -m pytest tests/test_forward_edge_session_runner.py -k 'paper_book_adapter_records_qualified_horizon_and_abstains_on_uncalibrated_factor' -q --basetemp=.pytest-checklist-forward-oms-units` — 1 passed.
- Ruff and scoped mypy passed for the adapter and test module.

## Remaining scope

FB-0103 remains PARTIAL. The session runner's persisted OMS requests, fills, paper-accounting records, and risk-sizing result values still need typed-value serialization and crash/restart recovery verification; other order adapters also need boundary audit.
