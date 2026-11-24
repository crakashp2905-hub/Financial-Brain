# Typed unit validation at fixture-pipeline ingress

Date: 2026-11-24  
Checklist item: FB-0103  
Status: PARTIAL

## Change

Before the compatibility fixture pipeline constructs a decision manifest or paper order, it validates order and trade quantities as whole units, displayed quote depth as nonnegative whole units, and execution prices, limits, bands, and tick size as positive INR prices. Invalid or fractional inputs now fail closed before legacy `int()` conversions can truncate them. Valid fixture outputs retain their historical signatures.

## Evidence

- `src/financial_brain/forward_edge/pipeline.py::_validate_fixture_financial_units`
- `src/financial_brain/forward_edge/pipeline.py::run_session`
- `tests/test_forward_edge_pipeline_compat.py::test_fixture_pipeline_rejects_invalid_order_units_before_execution`
- `tests/test_forward_edge_pipeline_compat.py::test_fixture_pipeline_rejects_fractional_quote_depth_before_execution`
- `tests/test_forward_edge_pipeline_compat.py` historical golden-signature cases remain unchanged.
- `python -m pytest tests/test_forward_edge_pipeline_compat.py tests/test_forward_edge_integrity_gates.py -q --basetemp=.pytest-checklist-forward-units-rerun` — 19 passed.
- Ruff and scoped mypy passed for the pipeline and test module.

## Remaining scope

FB-0103 remains PARTIAL. This compatibility pipeline covers equity-like whole-unit fixtures; the other provider adapters, full OMS/accounting serialized state, typed event outputs, and remaining quantity/price interfaces still need a repository-wide boundary audit.
