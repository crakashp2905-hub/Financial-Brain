---
type: research
tags:
  - research
  - strategy
  - firewall
date: 2026-09-25
---

# Slow trend is closed

Four pre-registered tests, one line of inquiry, and a complete answer. Worth writing down
because it is the only thing this project has ever chased to the end.

## The sequence

| Test | t | turnover | net | what it eliminated |
|---|---:|---:|---:|---|
| `above_ma200` | **+3.69** | 70%/period | -0.26% | - the only signal ever to clear Bonferroni |
| `ma_double_cross_50_200` | +2.74 | 0.78%/session | **+0.0195%** | - survives costs, misses significance |
| [[h11]] liquid universe only | +1.80 | 77%/period | -0.10% | the universe is not the answer |
| [[h12]] 5% hysteresis band | **-0.45** | 1.15%/session | -0.003% | turnover is not separable |

Along the way the cost assumption gating all of it was measured rather than believed -
see [[The cost number that decided everything]] - and found to be too *pessimistic*.
Correcting it in the direction that helps did not save the strategy, which is what makes
the correction credible rather than convenient.

## The finding

**The signal and the turnover are the same thing.**

`above_ma200` earns its t = +3.69 *at the crossing*. Every mechanism for reducing the
churn - waiting for a band, smoothing both legs, checking monthly - delays entry and exit
past that crossing, and removes exactly the part carrying the information. h12 is the
cleanest demonstration: a 5% band cut turnover threefold and took the t-statistic from
+2.74 to **-0.45**.

This is not "costs killed a good strategy". It is that the thing being harvested exists
only in the moments that generate the cost.

## What was eliminated, and how

Each step removed a different explanation rather than re-running the same test with new
parameters, which is why four trials were worth spending:

* **Is the effect real?** Yes - it cleared a Bonferroni bar of 3.29 at 3.69 across 11
  years, on a feature that had sat untested in the codebase since the first build because
  a boolean cast error stopped anyone who tried.
* **Is the cost estimate wrong?** Yes, and too pessimistic - and fixing it did not help.
* **Is it the universe?** No. Restricting to the most liquid 15% halved the signal (h11),
  because trend lives in smaller names, and cheaper costs did not compensate.
* **Is it the turnover?** No, and this is the answer. The turnover *is* the signal (h12).

## What is deliberately not being done

h12's excess reversed significantly in RISK_ON (t = +3.1) - the banded rule made money
there and lost elsewhere. A regime-conditional version is the obvious next fishing
expedition, and h12's pre-registration declared itself the last test of slow trend
precisely so that this moment would already have been decided.

Re-running a rejected strategy conditioned on where it happened to work is what the trial
counter exists to punish. It is noted here so the thought is recorded rather than acted on.

## Where that leaves the project

Fifty-nine trials, fifty-eight rejections, one void. The one genuinely promising line has
been followed to a definite end rather than left open, and the answer is that this
universe, at these costs, with daily data, offers nothing harvestable from slow trend.

The honest next moves are **different data** - intraday via [[Kite readiness]], which also
unlocks the four excluded intraday systems in [[Time-series strategies]] - or a
**different question** entirely. Not more parameters on this one.

Related: [[Trend is the only thing that has cleared significance]] ·
[[The cost number that decided everything]] · [[Why nothing passes]] · [[h11]] · [[h12]] ·
[[Alpha Validation Firewall]]
