# PE-0178 — Explicit factor and turnover regularization

## Acceptance

Compare candidates with explicit factor-exposure and expected-turnover regularizers so apparent alpha driven by size, momentum, beta, or excess trading is visible.

## Implementation and verification

`evaluation.factor_regularization` accepts development-only candidate forecasts, size/momentum/beta exposures, expected per-session turnover, and a versioned penalty policy. It reports raw forecast alpha, each factor exposure, factor drag, turnover drag, regularized forecast alpha, candidate count, and whether the specified penalties fully explain a positive raw forecast. The policy has a deterministic SHA-256 identity. Validation/test inputs are rejected, and the report is always marked non-promotable.

Verification: two factor/turnover tests passed with the challenger and learned-model suites (18 total); Ruff and targeted mypy passed.

## Limits

Regularization weights are explicit development policy inputs and must be selected before comparison; they are not empirically calibrated by this module. The adjusted value is a ranking diagnostic, not a causal factor attribution, validated alpha, or order authorization.
