# Typed risk-sizing outputs

Date: 2026-11-24  
Checklist item: FB-0103  
Status: PARTIAL

## Change

Risk-sizing results retain their established Decimal fields for compatibility and now expose dimensionally typed views for executable quantity, INR entry price, planned loss, and position notional. This lets callers carry unit meaning across the sizing boundary without changing the persisted numeric schema.

## Evidence

- `src/financial_brain/kernel/risk_sizing.py::RiskSizingResult.quantity_units`
- `src/financial_brain/kernel/risk_sizing.py::RiskSizingResult.entry_price_inr`
- `src/financial_brain/kernel/risk_sizing.py::RiskSizingResult.planned_loss`
- `src/financial_brain/kernel/risk_sizing.py::RiskSizingResult.position_notional`
- `tests/test_risk_sizing.py::test_sizing_result_exposes_dimensionally_typed_quantity_price_and_inr_values`
- `tests/test_risk_sizing.py::test_ineligible_sizing_result_keeps_zero_financial_outputs_typed`
- `python -m pytest tests/test_risk_sizing.py -q --basetemp=.pytest-checklist-risk-typed-output` — 7 passed.
- Ruff and scoped mypy passed for the module.

## Remaining scope

FB-0103 remains PARTIAL. The compatibility fixture pipeline and the repository's remaining provider, OMS, accounting and output boundaries still need a repository-wide unit-contract audit and integrations.
