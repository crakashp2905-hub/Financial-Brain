# Option lot typing in paper plans and replay

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

`option_lot_count` accepts the nominal `OptionLots` type or a strict positive integer compatibility input. It rejects booleans, floats, zero, and negative values. The long-option paper fill and cost-hurdle functions use it to calculate contract quantity; the plan uses the same boundary for visible depth and planned quantity; replay intents validate and normalize the typed value before persisting scalar-compatible journal fields.

Option quote bid/ask fields also accept `OptionPremiumPerUnit` alongside the legacy numeric form. Quote construction normalizes the nominal INR premium at the quote/cost-model boundary and rejects non-finite, non-positive, or crossed quotes.

## Evidence

- `src/financial_brain/kernel/units.py::OptionLots`
- `src/financial_brain/paper/options.py::option_lot_count`
- `src/financial_brain/paper/options_plan.py::build_long_option_plan`
- `src/financial_brain/paper/options_runner.py::Intent`
- `tests/test_paper_options.py::test_option_quote_accepts_nominal_inr_premium_per_unit`
- Focused options tests: `python -m pytest tests/test_paper_options.py tests/test_paper_options_plan.py tests/test_paper_options_runner.py tests/test_paper_options_forward.py -q --basetemp=.pytest-checklist-option-premium-rerun` — 20 passed.
- Ruff and scoped mypy passed for the modified option modules and tests.

## Remaining scope

FB-0103 remains PARTIAL. Long-option quote premiums and lots are typed at the local paper planner/replay boundary. Broker-independent forward OMS sizing, option risk aggregation, and typed output values across all paper books remain unproven end to end.
