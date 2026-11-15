# PE-0175 — Point-in-time feature availability for inference

## Acceptance

Require feature availability timestamps in inference and batch-training schemas; future-availability perturbation tests must demonstrate that those values cannot enter a prediction.

## Implementation and verification

`learned_model.score_frame` and `prediction_batch_sha256` now require timezone-aware decision, feature-availability, and feature-revision timestamps. Each cross-section has one decision timestamp, and every feature availability/revision must be at or before that decision; a revision cannot predate availability. Prediction batch hashes include these timestamps, and cache validation checks the same provenance. `score_day` requires an explicit aware decision time and reads availability/revision metadata from the feature relation; it fails closed if the relation lacks provenance. The offline training schema enforces the corresponding row-level timestamps and temporal ordering. Date-only historical book/zoo callers return no learned candidate rather than fabricate an inference clock.

Verification: forecast/model focused suite — 23 passed. Ruff, targeted mypy, source inventory and strategy-variant inventory checks passed (619 Python files; 253 candidates). Future-availability and future-revision perturbations are rejected.

## Limits

The learned feature relation must provide trustworthy point-in-time availability and revision timestamps. This change makes missing or future metadata unusable; it does not independently attest the upstream timestamps. Tests use synthetic timestamps and outcomes, so no real-market predictive quality is claimed.
