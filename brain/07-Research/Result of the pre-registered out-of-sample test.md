---
type: research
tags:
  - research
  - pre-registration
  - replication
  - validation
date: 2026-10-01
status: result
---

# Result of the pre-registered out-of-sample test

Predictions were committed in
[[PRE-REGISTERED out-of-sample test of the eight survivors]] at `09f2440`, before the test ran. Ranks
501 to 1500 had never been measured by anything in this project.

**All eight passed.** This is the first out-of-sample positive result this project has produced.

## The signs, all eight as predicted

    feature            pred          deep IC/t         micro IC/t   in-sample small   verdict
    vol_60               -1    -0.0810/ -24.98    -0.0915/ -28.40           -0.0678   PASS
    mom_12_1             +1    +0.0554/ +20.83    +0.0614/ +24.29           +0.0527   PASS
    dist_52w_high        +1    +0.0684/ +21.74    +0.0610/ +17.51           +0.0671   PASS
    atr_pctile_250       -1    -0.0488/ -23.48    -0.0636/ -23.05           -0.0299   PASS

    pattern                h  pred    effect         t   sessions    cases   verdict
    k_bearish_engulfing    5    -1    -1.09%    -10.72      2,347   23,055   PASS
    k_marubozu_bull        5    +1    +0.51%     +3.96      2,529   41,738   PASS
    k_shooting_star        5    -1    -0.55%     -5.00      2,475   30,593   PASS
    k_shooting_star       20    -1    -0.65%     -3.22      2,475   30,520   PASS

No refitting, no reselection, no new parameters. The same eight signals, the same methods, a
cross-section of 4,722 names that played no part in choosing them.

## The sharp prediction: three of four gradients continued

The registration said the monotone size gradient was the falsifiable part, and it mostly held:

    feature            large     next      mid    small     deep    micro
    mom_12_1          +0.024   +0.038   +0.040   +0.053   +0.055   +0.061   continues
    vol_60            -0.033   -0.033   -0.056   -0.068   -0.081   -0.092   continues
    atr_pctile_250    -0.038   -0.008   -0.028   -0.030   -0.049   -0.064   continues
    dist_52w_high     +0.016   +0.036   +0.050   +0.067   +0.068   +0.061   FLATTENS

`dist_52w_high` is the one that failed its sharp prediction: the IC rises to +0.068 at deep and then
*falls* to +0.061 at micro. The sign held, so the feature passes - but the claim that its gradient is a
property of the effect does not. **The 52-week-high effect peaks somewhere around rank 1,000 and stops
growing**, which momentum and volatility do not.

That is worth more than if all four had continued. A prediction that nothing can fail is not a
prediction, and the one feature that broke is the one whose gradient was steepest in sample - exactly
where a coincidence across four points was most likely.

`atr_pctile_250` is interesting the other way: it was *not* monotone in sample (-0.038, -0.008, -0.028,
-0.030) and becomes cleanly monotone out of it. Whatever made the `next` tier anomalous in sample was
local to it.

## The confound was eliminated, not argued away

The registration named staleness as the first thing to rule out: illiquid names have flat closes, a
flat close makes a late-arriving return look like continuation, and that would inflate a momentum IC
exactly where staleness is worst. Staleness does rise sharply:

    band        sessions with an exactly flat close
    1-500                                    0.84%
    deep                                     1.35%
    micro                                    3.68%

Four and a half times worse at micro. So every observation with a flat close was dropped and everything
re-measured:

    feature          tier       all obs      stale dropped     change
    mom_12_1         small      +0.0527            +0.0523    -0.0004
    mom_12_1         deep       +0.0554            +0.0555    +0.0001
    mom_12_1         micro      +0.0614            +0.0615    +0.0000
    vol_60           micro      -0.0915            -0.0909    +0.0005
    dist_52w_high    micro      +0.0610            +0.0603    -0.0008

**The largest change is 0.0008.** The gradient is unchanged and every sign and significance survives.
Staleness is real, rises as predicted, and is not what produces the effect.

Two things also point against the staleness story independently: it would have to *grow* the ICs where
staleness is worst, and `dist_52w_high`'s IC *shrinks* at micro where staleness peaks; and the
candle-pattern effects are matched event studies, which difference out anything shared by the control
names in the same session and bucket.

## What this is, stated carefully

**It is replication across the cross-section, not across time.** Every number here comes from
2016-04 to 2026-06, the same decade as the in-sample work. A phenomenon can be stable across the size
spectrum and still be an artifact of this particular decade. The time-series out-of-sample test remains
unrun and unrunnable on this archive - there is no window left that has not been looked at.

**The held-out tiers are not tradeable.** Median daily turnover is Rs 3.21 cr at deep and Rs 0.37 cr at
micro. These tiers test whether the **effect exists**, which is a different and weaker claim than
whether capital can be put behind it. Nothing here changes
[[Naive momentum beats it, and the horizon was a fit]]: the only strategy with positive out-of-sample
excess is still naive momentum at h60, and the costs still eat most of it.

**Four features are two ideas.** `vol_60` and `atr_pctile_250` are both volatility; `mom_12_1` and
`dist_52w_high` are both momentum. Six independently measured tiers each, with the predicted sign in
all six - but two underlying phenomena, both published decades ago.

**The candle effects weakened out of sample where it matters.** `k_marubozu_bull` fell from +0.95% to
+0.51% and its t from +6.28 to +3.96. It passed, and it halved. `k_bearish_engulfing` went the other way,
from -0.62% to -1.09%. Neither is a strategy at an 80 bp round trip and a five-session horizon.

## What changes from here

The methodology is now the finding. Pre-registering a prediction, committing it, then testing on a
subsample that had no part in the selection is the only procedure in this project that has produced a
positive result rather than destroyed one. Two walk-forwards and a baseline comparison killed everything
they touched; this passed eight of eight.

So the rule for what comes next: **nothing gets written up as promising before its held-out test exists
and is registered.** The ordering was the problem all along.

Related: [[PRE-REGISTERED out-of-sample test of the eight survivors]] ·
[[Four edges that replicate, and the chart patterns that do work]] ·
[[Naive momentum beats it, and the horizon was a fit]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]]
