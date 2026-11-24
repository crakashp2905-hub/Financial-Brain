# ETF intraday opening-range candidate v1.0.0

**Checklist item:** FB-0504  
**Status:** IMPLEMENTED SCREEN/CANDIDATE, NOT PERFORMANCE-EVALUATED  
**Protected trial status:** No authorization. Q4-2026 is exhausted; Q1-2027 is not authorized.

## Frozen screen

Implementation: `src/financial_brain/features/etf_intraday_screen.py`. The underlying observed bar aggregator is `src/financial_brain/features/etf_bars.py`; it now exposes contributing event IDs and a deterministic event-input hash for each bar.

- Use a sourced six-bar opening range from consecutive five-minute bars. Candidate direction is set only when the latest complete bar close crosses the opening-range high or low from the opposite side.
- Session open and close are supplied from the venue calendar. Every bar must be five minutes, contiguous, within the sourced session bounds, complete and available no later than timezone-aware `as_of`, and carry source event IDs plus a SHA-256 input hash. Missing bars reject the candidate.
- Filter quote events to those available by `as_of`; require the existing liquidity report to meet its caller-supplied gap, staleness, participation, cost, and quote-age limits.
- Require two-sided median top-of-book displayed depth to cover the caller's intended notional times `min_top_depth_multiple`.
- Use estimated round-trip cost = full quoted spread + Amihud-style size-impact proxy + caller-supplied other round-trip costs. This cost estimate is not calibrated execution evidence. The quoted spread is full spread because a buy-at-ask/sell-at-bid round trip crosses it once.
- Require the observed breakout extension to be at least 2x the estimated round-trip cost proxy. This is only a coarse plausibility screen on a past/current bar move; it is not an estimate of future return or P&L.
- Output a candidate or an explicit rejection/no-signal status. There is no order, position size, target, stop, fill, expected-alpha, or profitability output.
- Strategy ID/version: `ETF_INTRADAY_OPENING_RANGE_LIQUIDITY_SCREEN` v1.0.0. Any changes to bar interval, opening range, cost/depth gates, or signal rule require a new version and governance lineage.

## Evaluation boundary

The implementation is contract-tested only. No intraday backtest, parameter search, protected holdout evaluation, prospective trading cohort, fill calibration, or profitability claim was made. Protected research remains unauthorized (Q4-2026 exhausted; Q1-2027 not authorized).

Before any authorized evaluation, provide permitted minute/tick data, exact exchange calendars, full quote/depth coverage, actual cost schedule and taxes, order-size and participation assumptions, conservative fill/missed-fill models, passive/cash controls, dependence/multiplicity gates, capacity limits, and forward paper evidence. The Amihud size-impact proxy may not be reported as measured market impact.

## Verification

- `tests/test_etf_intraday_screen.py`, `tests/test_etf_bars.py`, and `tests/test_etf_liquidity.py` cover lineaged bars, point-in-time availability, contiguous sessions, cost/depth rejection, opening-range logic, and the round-trip spread arithmetic.
- Focused tests, Ruff, and scoped mypy establish implementation behavior only, not an edge.
