# PE-0081 — Slippage calibration

Calibration buckets cover spread, average daily traded value, time of day, participation, and volatility. Only timestamped, source-hashed `OBSERVED_BROKER` fills can produce calibration values. Missing samples remain `UNCALIBRATED`.

`register_calibrated_slippage()` allows complete observed-only outputs to create a new immutable execution-model version. It does not mutate an active model. Synthetic tests verify the guard; they are not calibration evidence. Authorized observed fills are still required before the system can claim market calibration.

Evidence and source hashes are in `pe-0081-slippage-calibration-evidence.json`.
