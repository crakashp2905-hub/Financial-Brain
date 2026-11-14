# PE-0171 — Reproducible training from matured outcomes

## Acceptance

Offline training must use timestamped, versioned outcomes that have matured by an explicit UTC training cutoff. The saved manifest binds the source dataset, fit rows, feature schema, training code, seed, model artifact, parameters and runtime versions. Unknown, incomplete or not-yet-matured labels are quarantined.

## Implementation and verification

`financial_brain.evaluation.offline_training.run_offline_training_job` accepts the learned challenger panel plus explicit dataset/schema versions and a timezone-aware `trained_as_of`. It rejects missing or mixed versions, naive decision/maturity timestamps and duplicate date/lineage rows. Only rows with a decision and complete feature/label values whose label maturity timestamp is known and no later than the cutoff enter fitting. Other rows are counted as quarantined. The deterministic, single-thread LightGBM fit emits a content-addressed manifest with hashes for the timestamped dataset, exact fit data, feature schema, relevant code and model; the manifest also records the seed, trainer parameters and Python/numpy/pandas/LightGBM versions.

`learned_model.load` verifies the manifest and model lineage before using a model. The prediction-cache path performs the same manifest check, so existing cached predictions cannot make an unmanifested legacy model usable. The lower-level `train_final` helper remains a fitter for internal use and does not create a loadable artifact by itself.

Verification: `tests/test_phase5_learned_model.py` — 10 passed after the PE-0172 temporal-split extension. Ruff and targeted mypy passed. Source inventory and strategy-variant inventory checks passed (618 Python files; 253 source-linked strategy candidates).

## Limits

Tests use synthetic panels and synthetic maturity timestamps. This proves cutoff enforcement, quarantine, hashing and load-time integrity; it does not establish model quality, profitability, forward validation or provenance of any real financial outcome. The caller must supply a trustworthy dataset version and outcome-maturity timestamps. PE-0172 adds training-time availability and revision checks; PE-0175 extends point-in-time availability contracts to inference and batch schemas.
