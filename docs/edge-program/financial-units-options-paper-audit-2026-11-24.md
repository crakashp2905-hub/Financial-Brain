# Option lot typing in paper plans and replay

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

`option_lot_count` accepts the nominal `OptionLots` type or a strict positive integer compatibility input. It rejects booleans, floats, zero, and negative values. The long-option paper fill and cost-hurdle functions use it to calculate contract quantity; the plan uses the same boundary for visible depth and planned quantity; replay intents validate and normalize the typed value before persisting scalar-compatible journal fields.

## Evidence

- `src/financial_brain/kernel/units.py::OptionLots`
- `src/financial_brain/paper/options.py::option_lot_count`
- `src/financial_brain/paper/options_plan.py::build_long_option_plan`
- `src/financial_brain/paper/options_runner.py::Intent`
- Focused options tests: `python -m pytest tests/test_paper_options.py tests/test_paper_options_plan.py tests/test_paper_options_runner.py tests/test_paper_options_forward.py -q --basetemp=.pytest-checklist-options` — 19 passed.
- Ruff and scoped mypy passed for the modified option modules and tests.

## Remaining scope

FB-0103 remains PARTIAL. This change covers the option-lot boundary for the long-premium paper planner/replay, but broker-independent forward OMS sizing, option risk aggregation, mutual-fund fractional units/NAV, and persisted typed quantities across all paper books are not yet proven end to end.
