# PE-0161 — Training-only intraday selectivity and same-time controls

## Implemented

- Added a training-only threshold fitter for intraday candidate scores. Its API accepts only rows tagged `TRAIN` inside an explicit frozen training date window; validation/forward partitions and dates outside the window are rejected.
- Candidate and control samples must have distinct hashed source manifests and disjoint event identities. Controls must be a different symbol in the same exchange session, time bucket, and predeclared stratum; each control is matched once by deterministic pre-event covariate distance. A threshold with even one unmatched selected event is ineligible.
- Training outcomes are computed net of the supplied round-trip costs. The frozen threshold maximizes mean matched net excess on training only; deterministic ties prefer fewer selected events and then the stricter threshold. The output reports matched event count, selected event count, sessions, source hashes, and the training window.
- The frozen policy can be applied to scores without inspecting outcomes. It contains no forward schedule digest and remains labeled `TRAINING_ONLY_EXPLORATORY`; this module does not run or select on a forward cohort.

## Verification

`tests/test_intraday_selectivity.py`: 4 passed; Ruff passed. Coverage verifies threshold selection, net-of-cost same-time matching, missing-control refusal, independent source identities, and rejection of forward-tagged or out-of-window rows.

## Research boundary

This is a controlled fitting primitive, not a result for ORB, GAP, or any live strategy. Its usefulness depends on truthful partition manifests, point-in-time data, pre-event matching covariates, and a separately frozen evaluation protocol. No profitable edge is claimed.
