# Typed Kite quote prices and share depth

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

Kite last/bid/ask, OHLC, and circuit-band values pass through INR `Price` validation before the event payload's compatibility scalar encoding. Volume, top-of-book size, and displayed depth levels pass through `WholeUnits` validation. Fractional quantities are rejected rather than converted with `int()` truncation; malformed deeper levels make the quote non-executable.

## Evidence

- `src/financial_brain/forward_edge/market_data.py::_inr_price`
- `src/financial_brain/forward_edge/market_data.py::_whole_units`
- `src/financial_brain/forward_edge/market_data.py::normalize_kite_quote`
- `src/financial_brain/forward_edge/market_data.py::executable_quote`
- `tests/test_forward_edge_kite_market_data.py::test_kite_price_and_depth_inputs_obey_inr_price_and_whole_share_units`
- `python -m pytest tests/test_forward_edge_kite_market_data.py -q --basetemp=.pytest-checklist-kite-units-final` — 16 passed.
- Ruff and scoped mypy passed for the market-data module and tests.

## Remaining scope

FB-0103 remains PARTIAL. Other provider adapters, the full OMS/accounting serialized state and all remaining quantity/price interfaces still require a repository-wide boundary audit.
