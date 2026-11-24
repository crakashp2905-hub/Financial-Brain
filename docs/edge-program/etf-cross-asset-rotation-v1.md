# ETF cross-asset rotation candidate v1.0.0

**Checklist item:** FB-0503  
**Status:** FROZEN IMPLEMENTATION CANDIDATE, NOT EVALUATED  
**Protected trial status:** No authorization. Q4-2026 is exhausted; Q1-2027 is not authorized.

## Frozen snapshot rule

Implementation: `src/financial_brain/features/etf_cross_asset_rotation.py`. The rule specification and input set are hashed into every output snapshot.

- Eligible sleeves are equity (broad, sector, and factor exposures grouped as one sleeve), debt, gold, and silver. Other/unknown and international exposures are excluded from this rule version.
- Within each sleeve, select the eligible ETF with the largest exact 126-session close-to-close return. Return ties resolve deterministically by ISIN.
- The rotation candidate selects the highest-return sleeve winner only when its 126-session return is positive. Otherwise the rotation weight is cash.
- The rotation risk-sizing reference uses the selected ETF's sample volatility over the latest 20 adjacent sourced returns, targets 10% annualized volatility using 252 sessions per year, and caps gross exposure at 100%.
- `equal_volatility_control` assigns normalized inverse-20-session-volatility weights to each sleeve winner. It is an inverse-volatility proxy and ignores covariance.
- `buy_and_hold_control` starts equal-weight across sleeve winners at this formation snapshot. Evaluators must freeze these initial weights and not refresh them when measuring buy-and-hold.
- `same_risk_control` equal-weights the sleeve winners and scales that snapshot to a 10% trailing 20-session portfolio volatility target, capped at 100% gross. This is an ex-ante volatility proxy, not ex-post matched risk.
- `cash_control` has no ETF exposure and 100% cash. No cash yield is assumed by this candidate.
- Require 127 exact expected-session closes, lowercase SHA-256 source hashes, and timezone-aware availability no later than `as_of` for each selected ETF history. Missing data is not filled or bridged; reference rows must be effective, published, and observed by `as_of`.
- Outputs are a single candidate allocation snapshot only. The module does not compound returns, model execution costs/tax, place orders, or assert profitability.
- Strategy ID/version: `ETF_CROSS_ASSET_ROTATION` v1.0.0. Changes to universe, ranking horizon, rebalance schedule, sizing, controls, costs, or data policy require a new version and governance lineage.

## Evaluation boundary

Focused tests establish the code contract only. They do not authorize a historical strategy evaluation, protected holdout access, parameter selection, or a prospective confirmatory cohort. The protected Q4-2026 budget is exhausted and Q1-2027 is not authorized. No performance or cost claims are made.

An approved evaluation charter still needs point-in-time ETF universe history, delistings/corporate actions, cost and tax schedules, rebalance/turnover accounting, cash return series, benchmark definitions, missing-data handling, dependence/multiplicity gates, capacity limits, and forward paper protocol. The same-risk control's trailing volatility estimate must be reported separately from realized cohort risk.

## Verification

- `tests/test_etf_cross_asset_rotation.py`: five contract tests cover sleeve grouping/selection, all control weight sums, cash behavior for negative momentum, deterministic hashes, missing/late input exclusion, calendar, hash, timestamp, and future-session rejection, plus the no-order boundary.
- Focused pytest, Ruff, and scoped mypy are implementation checks only, not strategy-performance evaluation.
