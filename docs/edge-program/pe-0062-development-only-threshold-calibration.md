# PE-0062 development-only net-alpha hurdle calibration

Added a development-only calibrator for predeclared non-negative hurdle candidates. It refuses validation/test rows, observations on or after the validation start, duplicate decision identities, insufficient samples, non-finite values, and policies frozen on or after validation begins. Candidate selection maximizes a 95% lower confidence bound of realized net alpha among development observations that clear each candidate threshold; the selected threshold, development data digest, protocol digest, dates, observation count, score, and freeze timestamp form one content-addressed policy.

`NetAlphaThresholdRegistry` persists that policy in the existing hash-chained durable journal and rejects a changed policy for the same strategy/cohort. It also records immutable forward-drawdown receipts. `ForwardSessionRunner` can require a policy registry, verifies the policy hash and threshold against the session configuration, rejects sessions before the frozen validation start, and prevents a strategy/cohort from changing either its threshold or policy hash in a shared journal.

## Evidence and limits

The tests use synthetic observations only. No real training slice has been calibrated and no production threshold is claimed. The lower-confidence-bound candidate selector is a development aid, not a multiplicity-corrected promotion test; confirmation must use a frozen, untouched validation cohort and a separately approved statistical protocol.
