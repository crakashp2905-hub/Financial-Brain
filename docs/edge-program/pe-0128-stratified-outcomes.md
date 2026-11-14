# PE-0128 — stratified performance and fragmentation uncertainty

`stratify_outcomes()` requires a registered label set for volatility, market regime, liquidity, market-cap band and transaction-cost bucket. It reports each registered stratum, including empty buckets, against the full scheduled-session denominator. Missing dates withhold confidence bounds. Complete buckets receive simultaneous Newey-West bands with Bonferroni allocation across every registered bucket and both net-alpha and after-cost utility metrics. A positive bucket is not promoted to a global profitability claim.

**Status: PARTIAL.** Nine focused tests and Ruff pass. The calculation contract is implemented, but the stratum registry, portfolio aggregation weights and regime labels are still caller-supplied and are not bound to the authoritative prospective cohort. No market cohort data is available here.

Input rows must represent one already-aggregated outcome per dimension, bucket and session; callers remain responsible for freezing how instrument outcomes and portfolio utility are allocated before observing results.

Evidence and source hashes are recorded in `pe-0128-stratified-outcomes-evidence.json`.
