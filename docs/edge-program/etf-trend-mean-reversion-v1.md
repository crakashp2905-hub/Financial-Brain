# ETF trend and mean-reversion candidate v1.0.0

**Checklist item:** FB-0502  
**Status:** FROZEN IMPLEMENTATION CANDIDATE, NOT EVALUATED  
**Protected trial status:** No authorization. Q4-2026 is exhausted; Q1-2027 is not authorized.

## Frozen candidate rules

The implementation is in `src/financial_brain/features/etf_trend_rules.py`. The canonical rule specification is exported as `SPEC`, with SHA-256 `SPEC_SHA256`; every candidate carries that digest and the input-history digest.

- Trend continuation candidate: latest close above the 50-session simple moving average, with the 50-session average above the 200-session average.
- Faster moving-average candidate: latest close above the 20-session average, with the 20-session average above the 50-session average.
- Medium-horizon mean-reversion candidate: latest close is at least two sample standard deviations below the 20-session mean. Candidate exit reference is a zero z-score; no order or exit is emitted.
- Volatility sizing reference: target 10% annualized volatility using 20 adjacent simple returns and 252 sessions per year; exposure is clipped to `[0, 1]`. This is a sizing calculation only, not capital approval or an instruction to allocate.
- Require 201 exact consecutive expected-session closes and a lowercase SHA-256 for each selected input. Missing closes are not filled, bridged, or silently shifted. Zero-volatility and invalid-price cases emit no candidate or fail closed.
- All signals are computed at the latest observed close for later-session review. The module has no execution path and marks every row `candidate_only=true`, `orders_authorized=false`.
- Strategy identity is `ETF_TREND_MEAN_REVERSION` version `1.0.0`. Any changed threshold, window, sizing target, execution model, or universe requires a new version and governance lineage.

## Evaluation boundary

The frozen rules and contract tests establish a reproducible implementation only. They do not authorize historical parameter search, decisive backtests, protected holdout access, or a prospective confirmatory cohort. No Q4-2026 budget remains, and Q1-2027 is `NOT_AUTHORIZED`; a human governance amendment and exact-scope approval are required before protected evaluation. Do not tune or select among these rules using the locked 13-trial Q4 ledger.

No performance, cost, tax, capacity, drawdown, benchmark-superiority, or profitability claim is made. Before evaluation, define the universe, survivorship and corporate-action handling, point-in-time input availability, benchmark/passive/cash controls, Indian transaction costs and taxes, missing-data policy, overlap/correlation treatment, multiplicity plan, confidence thresholds, and forward paper protocol in a separately approved charter.

## Verification

- `tests/test_etf_trend_rules.py`: four contract tests cover the fixed flags and spec hash, no-fill history requirements, calendar/hash rejection, volatility sizing bounds, repeatability, and candidate-only execution boundary.
- Focused tests, Ruff, and scoped mypy verify code behavior only; they are not strategy-performance experiments.
