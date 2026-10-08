---
type: research
tags:
  - research
  - result
  - edges
date: 2026-10-08
status: result
---

# Result of edges batch two

Tests [[PRE-REGISTERED edges batch two]]. Same bar: net mean positive with t >= 2 in both periods. F&O membership
from the futures history (213 names, about 115 a session; the history's F&O panel is thin before 2019, so the
first period is mostly 2019-2021). Series in `data/edges/`.

| Candidate | 2016-21 net | t | 2022-26 net | t | Gross / cost | Verdict |
|---|---:|---:|---:|---:|---|---|
| W1 5-session reversal, futures L/S, weekly | -0.300% | -1.17 | -0.192% | -1.40 | gross -0.058%, cost 0.176% | fail |
| W2 52-week-high, futures L/S, weekly | -0.011% | -0.03 | -0.188% | -1.26 | gross -0.059%, cost 0.061% | fail |
| W3 12-1 momentum, futures L/S, monthly | -0.057% | -0.04 | +0.333% | +0.53 | gross +0.241%, cost 0.059% | fail |
| E1 momentum ensemble, top 20, monthly (excess) | +0.406% | +0.68 | -0.054% | -0.11 | 2022-26 total Sharpe 0.36 | fail |
| X1 Nifty overnight (close to open), daily | +0.105% | **+5.88** | +0.019% | +1.12 | | fail |
| X2 Nifty opening-overreaction fade (145 events) | -0.103% | -0.46 | -0.011% | -0.11 | gross -2.7 bp | fail |

* **C1 wrong.** There is no short-term reversal to rescue among F&O names: the long-short is negative *gross*. The
  5-session reversal seen in the wider 500 lives in smaller names, which have no futures.
* **C2 wrong in part.** W3 is positive in 2022-26 but not 2016-21; W2 is negative in 2022-26.
* **C3 wrong.** E1's 2022-26 Sharpe (0.36) is below every part's (0.73-0.82): restricting the ensemble to the
  continuous-information half and averaging ranks diluted the strongest signal.
* **C4 right.** X1 is the strongest single number of the day (+10.5 bp a day net, t 5.9, in 2016-21) and it faded to
  +1.9 bp (t 1.1) since 2022 - the overnight premium of the last decade is largely gone. X2 has no signal.

All six are paper-traded in the zoo with these verdicts beside them (W1-W3 in futures, E1 delivery, X1 settled from
the next morning's open, X2 from the day's minute bars), because the owner asked that everything built be tried.

Information, not advice.
