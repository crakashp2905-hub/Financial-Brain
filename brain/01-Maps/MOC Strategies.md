---
type: map
tags:
  - map
  - strategy
updated: 2026-09-22
---

# MOC Strategies

The playbook, and what our own data says about each entry. Every note carries the same
four things: **what it claims**, **how it is constructed**, **our measured verdict**, and
**what would change the answer**. A strategy with no verdict has not been tested here and
is not a candidate.

| Strategy | Verdict here | Killed by |
|---|---|---|
| [[Momentum]] | REJECT | deflated Sharpe after 19 trials |
| [[Low volatility]] | REJECT | costs: -0.37%/period at 25% turnover |
| [[Short-term reversal]] | REJECT | costs: -0.78%/period at 81% turnover |
| [[Event-driven news flow]] | REJECT | IC **t = -2.00**, the opposite sign to the prior |
| [[Following disclosed insiders]] | **VOID** | the feature held no dealing data |
| [[h8]] - dealing only | REJECT | t = -0.45 on the corrected population |
| ma_cross_200 | REJECT | t = -3.82; whipsaw, 3.75%/session turnover |
| **ma_double_cross_50_200** | REJECT | **t = +2.74, needed 3.19** - see [[The golden cross and the bar it did not clear]] |
| faber_taa_10m | REJECT | t = +1.38 |
| bollinger_reversion_20_2 | REJECT | t = -6.75; catching knives at 8%/session turnover |
| turtle_20_10 | REJECT | t = -5.20 |
| **above_ma200** | REJECT | **t = +3.69 - clears significance**, dies at 70% turnover |
| [[h11]] - above_ma200, liquid only | REJECT | t fell to +1.80; trend lives in illiquid names |
| [[h12]] - above_ma200 + 5% band | REJECT | t -0.45 - but the band was **static**, see [[h13]] |
| [[h13]] - ATR-scaled band | REJECT | t +0.43, net **positive**, **cost gate PASS** |
| [[h13]] - + variance-ratio gate | REJECT | highest gross of all, churns 6x |
| [[h14]] - + India VIX stand-down | REJECT | t -2.85; missed rebounds beat avoided losses |
| [[h15]] - opening_range (intraday) | REJECT | gross **+0.043%**, cost 16x the edge |
| [[h15]] - dual_thrust | REJECT | gross **+0.096%**, cost 7x the edge |
| [[h15]] - r_breaker | REJECT | gross +0.060%, cost 12x |
| [[h15]] - dynamic_breakout_ii | REJECT | gross +0.028%, cost 25x |
| [[h16]] - **CLARIFICATION** | REJECT as a trade | **t = -6.73** on 191 names - usable as a *screen* |
| [[h16]] - JOINT_VENTURE | REJECT | best net +0.0417%, passes cost, **capacity 10 names** |
| [[h16]] - ACQUISITION | REJECT | +0.0141%, passes cost and capacity, t = +2.09 |
| [[h16]] - nine other event types | REJECT | see [[The archive screens out, it does not pick]] |
| intraday candles | REJECT | apparent reversal was **bid-ask bounce** |

**Read against the null, not against zero.** [[Running the strategies on noise]] shows a
strategy's own t conflates signal with cost drag. Turtle is third-worst by absolute t and
has the *largest* separation from its turnover-matched null; the two band variants score
*below* theirs.
| above_ma50 | REJECT | t = +1.44 |
| rsi/stoch/williams/cci | REJECT | |t| < 0.7 on ~80% turnover |
| macd_hist, mfi, obv_slope | REJECT | t -1.6 to -2.9, costs |
| atr_14_pct | REJECT | t = -3.82; low volatility, a third time |
| adx_14 | REJECT | t = +1.57; strength without direction, as predicted |
| 7 candlestick patterns | REJECT | all negative; control fired - [[Charts, candles and indicators]] |
| [[Governance red flags]] | REJECT | costs at 80% turnover |
| [[Timing model]] | REJECT | the benchmark was the median; see [[Beating the median is not an edge]] |

Seventy-eight trials, seventy-seven rejections and one **void** - and since 2026-09-23 the list
includes stateful entry/exit systems, which [[Time-series strategies]] made evaluable for
the first time. One signal cleared the significance gate - `above_ma200` at **t = +3.69** against a bar of
3.29 - and died on turnover. It was then chased to a definite end over four pre-registered
tests: the universe was not the answer ([[h11]]), the cost estimate was too pessimistic and
fixing it did not help, and reducing the turnover destroyed the signal ([[h12]]). Then a static-rule objection reopened it: scaling the band to each name's own volatility
nearly doubled the gross and **passed the cost gate**, the first trend strategy to do so.
What remains is about **+0.6% a year at t = +0.43** - not the loss h12 concluded, and not
tradeable. [[Dynamic rules, and the correction to h12]]. The insider test measured a feature that
did not contain insider trades; re-run on the corrected population it still failed, which
narrows the open question to *direction* rather than leaving it open. That is the honest
state, and the list is the point:
each note records why, so the next attempt starts from the failure rather than repeating
it.

Related: [[Alpha Validation Firewall]] · [[Procedural memory]] · [[The control]]
