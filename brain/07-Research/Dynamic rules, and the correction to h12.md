---
type: research
tags:
  - research
  - strategy
  - mistake
  - firewall
date: 2026-09-25
---

# Dynamic rules, and the correction to h12

[[Slow trend is closed]] said "the signal and the turnover are the same thing" and declared
the line finished. **That was too strong.** It was an artifact of a static rule, and the
objection that caught it came from outside: every rule this project had tested applied the
same numbers to every stock.

## The defect, quantified

A fixed 5% hysteresis band (h12) on a universe whose 14-session ATR runs from **2.31% at
the 10th percentile to 6.36% at the 90th**:

    5% band on a p10 (quiet) name    = 2.2 ATR
    5% band on a p90 (volatile) name = 0.8 ATR

That is not one rule with one parameter. It is hundreds of different rules, and the
parameter that mattered was never chosen. On quiet names the band was a moat wide enough
to delete the signal entirely.

## What scaling the band did

    fixed 5% band        gross +0.00557%   cost 0.00818%   net -0.00261%   turn 1.154%
    ATR-scaled band      gross +0.00975%   cost 0.00722%   net +0.00253%   turn 1.018%
    ATR band + VR gate   gross +0.01327%   cost 0.04516%   net -0.03189%   turn 6.373%

Three things worth reading off that table:

1. **The gross excess rises monotonically** with each adaptive refinement. The direction is
   right, and it is right for a stated reason rather than by search.
2. **The ATR band nearly doubled the gross AND cut turnover.** Net turned positive, and the
   **cost gate passed** - the first time any trend strategy has cleared it.
3. **The variance-ratio gate produced the highest gross of the three**, which supports the
   dilution hypothesis: only **37.7%** of liquid observations have `vr_60 > 1`, so an
   unconditioned trend rule spends most of its time on names whose moves reverse.

## Why none of it passes

`above_ma200_atr_band`: **t = +0.43**, net +0.0025% a session - about **+0.63% a year**.
Positive, and statistically indistinguishable from zero.

`trend_only_when_trending`: the VR gate flips constantly because VR crosses 1 constantly,
so turnover jumps sixfold and costs 0.045% a session against a 0.013% gross. Its
significance gate *passes* at t = -3.91, which is significance about the cost rather than
the signal.

`trend_calm_vix` ([[h14]]): standing down when India VIX is in the top fifth of its year
made things much worse, t = -2.85 and net -0.16% a session. It is invested only 81% of
sessions, and in a right-skewed universe the missed rebounds exceeded the avoided losses.
A market-wide gate also flips the whole book at once, tripling turnover.

## The correction, stated plainly

h12's conclusion was wrong in its strong form. **Turnover can be separated from the signal,
if the separation is scaled to each name.** Doing it properly moves slow trend from a small
loss to a small gain.

What is left when it is done properly is about +0.6% a year at t = +0.43. So the *revised*
conclusion is narrower and better supported: slow trend in Indian equities, harvested with
volatility-scaled rules, is approximately **zero after costs** - not negative, as h12
concluded, and not tradeable either.

Being wrong in the direction of over-closing a line of inquiry is the failure mode worth
noting. The firewall protects against believing things too easily; nothing in it protects
against dismissing them too easily, and that job fell to an outside objection.

## Also built today

* **`features/behaviour.py`** - per-name variance ratio and own-history volatility
  percentile, both strictly backward-looking. The median liquid Indian stock has
  `vr_60 = 0.916`: mildly **mean-reverting**, not trending. That is a finding in itself.
* **India VIX wired in** - it had been sitting in `index_levels` since 2015, 2,894 sessions,
  never used as a feature.
* **The legitimacy line**, written into the module: a rule may vary per stock when one fixed
  formula reads that name's *trailing* statistics; it may not vary because a parameter was
  fitted per name on the data it is judged against.

Related: [[Slow trend is closed]] · [[h13]] · [[h14]] · [[Why nothing passes]] ·
[[The cost number that decided everything]] · [[Alpha Validation Firewall]]
