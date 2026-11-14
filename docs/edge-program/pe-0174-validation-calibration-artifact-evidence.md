# PE-0174 — Expected-net-return and uncertainty validation report

## Acceptance

Produce expected-net-return and uncertainty calibration curves plus a trading-impact plot on an untouched internal validation cohort.

## Implementation and verification

`write_alpha_validation_artifacts` accepts only rows marked `VALIDATION`, one fixed alpha model/target schema, unique decision digests, and matured/revised targets known by an explicit timezone-aware cutoff. It writes a deterministic JSON report and dependency-free SVG containing: equal-count expected-versus-realized net-alpha-bps bins, empirical coverage of forecast uncertainty intervals by standard-error bin, and realized net-alpha bps against the fraction of validation decisions selected at forecast quantiles. The JSON and SVG bind to the validation sample digest and model identity and include artifact hashes. The function reports validation results only; it does not fit or retune a policy.

Verification: `tests/test_forecast_components.py` — 12 passed, including generated-artifact content and digest checks. Ruff and targeted mypy passed. The combined forecast-component and learned-model test set has 22 passing tests. Source and strategy-variant inventories remain current at 619 Python files and 253 source-linked candidates.

## Limits

The tested dataset is synthetic. No real calibration, forward quality, or profitable trading result is asserted. A production report must be built from a genuinely untouched, point-in-time validation cohort with reviewed data provenance; policy changes after viewing it require a new holdout.
