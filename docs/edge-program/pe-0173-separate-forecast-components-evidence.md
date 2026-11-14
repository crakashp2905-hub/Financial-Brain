# PE-0173 — Separate alpha, fill, and risk forecasts

## Acceptance

The supervised alpha forecast, execution fill-probability forecast, and risk forecast use distinct typed outputs, target definitions, model identities, and independent quality diagnostics.

## Implementation and verification

`financial_brain.evaluation.forecast_components` defines immutable `AlphaForecast`, `FillProbabilityForecast`, `RiskForecast`, and `ForecastBundle` contracts. The target schemas are explicitly distinct: realized net alpha in basis points, filled-by-deadline as a binary event, and realized adverse excursion in basis points. Component evaluators accept only untouched `VALIDATION` rows from one model version and one target schema, with timezone-aware matured and revised labels known by the report cutoff.

Development-only calibrators are distinct: alpha uses an affine forecast-to-realized-net-alpha fit; fill probability uses a monotone pool-adjacent-violators map; downside risk scales predicted loss quantiles to empirical development coverage. Their artifacts bind the component model, target schema, development data and timestamps, validation start, and freeze time. Untouched validation reports are also component-specific: alpha reports MAE, RMSE, bias, and regression calibration slope/intercept; fill probability reports Brier score, log loss, reliability bins, and expected calibration error; risk quantiles report empirical coverage, coverage error, underprediction rate, and pinball loss. No component is inferred from another or used to authorize trading.

Verification: `tests/test_forecast_components.py` and `tests/test_phase5_learned_model.py` — 23 passed. Ruff and targeted mypy passed. Source inventory freshness check and strategy-variant inventory check passed (619 Python files; 253 source-linked candidates). Tests use synthetic observations only; no live model calibration is claimed.

## Limits

These calibrators are engineering implementations validated on synthetic data. Component-specific real development datasets, fitted model artifacts, and untouched validation cohorts are still required before any empirical quality claim or paper decision can rely on the outputs. No real execution-fill, risk, or alpha calibration is asserted.
