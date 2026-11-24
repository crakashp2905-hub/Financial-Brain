# ETF cross-sectional momentum candidate v1.1.0

**Checklist item:** FB-0501  
**Status:** IMPLEMENTED CANDIDATE, NOT EVALUATED  
**Research approval:** Required before any decisive historical or prospective performance evaluation.

## Frozen rule

- Compare an ETF only with records sharing the same exchange, explicitly sourced underlying total-return index, and explicit exposure category.
- Use exactly 21, 63, 126, and 252 expected-session close-to-close returns, with equal weight across horizons.
- Convert each horizon return to a cross-sectional percentile rank within that comparison group; average the four percentiles. Higher ranks sort first. Ties receive the same average rank.
- Require exact sourced close values at both endpoints of every horizon, with timezone-aware availability no later than the decision time. Do not fill or bridge missing values.
- Require an ordered, unique expected-session calendar supplied by the caller; reject duplicate or unsorted calendar entries.
- Require lowercase SHA-256 source hashes for each selected endpoint and emit an aggregate endpoint-input digest with the candidate.
- Exclude `UNKNOWN` exposure, missing underlying index, ineffective or expired reference rows, and reference rows not published and observed by the decision timestamp.
- Emit candidates for research review only. No order, price forecast, expected alpha, capacity claim, or profitability assertion is produced.
- Tag each candidate with strategy ID `ETF_CS_MOMENTUM`, version `1.1.0`, and the frozen spec hash.

Version 1.1.0 keeps the same signal horizons, equal weights, grouping and tie rule as the initially committed 1.0.0 contract. It strengthens input eligibility by requiring per-endpoint source hashes and availability no later than `as_of`; its spec hash therefore differs from 1.0.0. Version 1.0.0 was not performance-evaluated or promoted.

## Evaluation boundary

This specification is frozen before any strategy-performance evaluation. Unit and contract tests verify implementation behavior only; they are not a decisive strategy test. Historical evaluation, parameter changes, candidate promotion, and forward paper experiments require the research authorization and trial-budget controls in FB-0004. Any changed horizons, weights, grouping, or missing-data policy require a new strategy version.

Transaction costs, tracking difference, spreads, taxes, liquidity, turnover, drawdown, and matched passive controls are not evaluated by this ranker. A candidate is not eligible for deployment until those controls and prospective paper evidence are separately implemented and approved.
