---
type: research
tags:
  - research
  - pre-registration
  - intraday
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED intraday features

Written and committed before the study runs. `features/intraday.py` builds five first-hour features per
(instrument, session) from `minute_bars` (100 instruments, 2024-09 to 2026-09, ~497 sessions) and two
outcomes, both starting at the 10:30 close: `r_to_close` and `r_to_next_close`.

Test: per-session cross-sectional Spearman IC against the cross-sectionally demeaned outcome, then the
t-statistic of the daily IC series. Ten feature x outcome trials, so the bar is the project's running
Bonferroni bar (about 3.7) and not 1.96. Power is limited (100 names); a null here is "not detected", not
"absent".

## Predictions

* **N1 gap** fades after 10:30 (negative IC to `r_to_close`) but |t| < 3.7.
* **N2 or_breakout** continues (positive IC) but |t| < 3.7. Opening-range breakout is the most popular
  retail intraday rule; I expect it to be arbitraged to nothing at this size and cost.
* **N3 vwap_dev** mean-reverts (negative IC) with |t| < 3.7.
* **N4 rvol_30** has no directional IC (|t| < 3.7).
* **N5 range_1h predicts the *size* of the afternoon move, not its direction**: IC against
  `|r_to_close|` positive with t > 10, and no directional IC. A volatility result, not an edge.
* **N6 nothing is tradeable.** No directional feature clears the bar, and a mean |IC| of ~0.02 on a
  ~40-bp mean absolute afternoon move is smaller than an intraday round trip.

Failures reported as failures; no feature, minute or outcome is added after seeing the result.
