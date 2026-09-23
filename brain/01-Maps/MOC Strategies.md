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
| [[Governance red flags]] | REJECT | costs at 80% turnover |
| [[Timing model]] | REJECT | the benchmark was the median; see [[Beating the median is not an edge]] |

Thirteen trials, twelve rejections and one **void** - and since 2026-09-23 the list
includes stateful entry/exit systems, which [[Time-series strategies]] made evaluable for
the first time. The closest any candidate has come is the golden cross at t = +2.74
against a Bonferroni bar of 3.19: a raw p of 0.0061 that a naive write-up would publish. The insider test measured a feature that
did not contain insider trades; re-run on the corrected population it still failed, which
narrows the open question to *direction* rather than leaving it open. That is the honest
state, and the list is the point:
each note records why, so the next attempt starts from the failure rather than repeating
it.

Related: [[Alpha Validation Firewall]] · [[Procedural memory]] · [[The control]]
