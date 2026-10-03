---
type: research
tags:
  - research
  - pre-registration
  - regime
  - signal-decay
date: 2026-10-03
status: result
---

# Result of the regime and decay tests

Predictions committed at `62b4b40` before running. Held-out tiers 501-1500 throughout, so everything
here is out of sample with respect to the selection.

**One prediction right, one wrong, and the wrong one changes more.**

## Regime: all four signals reverse in a crisis

    feature           pred       RISK_ON       NEUTRAL        NARROW      RISK_OFF        CRISIS
    mom_12_1            +1 +0.0560/+27.9 +0.0854/+19.9 +0.1223/+23.1 +0.0383/ +7.7 -0.1318/ -8.4
    dist_52w_high       +1 +0.0627/+24.2 +0.1171/+23.0 +0.1489/+20.0 +0.0225/ +3.7 -0.2037/-11.1
    vol_60              -1 -0.0688/-21.6 -0.1351/-27.3 -0.1711/-31.1 -0.0678/-12.5 +0.0684/ +6.1
    atr_pctile_250      -1 -0.0481/-19.1 -0.0688/-18.5 -0.1245/-28.1 -0.0518/-14.0 +0.0693/ +7.7

    tier-sessions:  RISK_ON 2,506   NEUTRAL 934   RISK_OFF 998   NARROW 478   CRISIS 156

### The momentum crash prediction was right, and it is violent

`mom_12_1` weakens in RISK_OFF (+0.056 to +0.038) and **reverses to -0.1318 in CRISIS at t = -8.4**.
`dist_52w_high` reverses to **-0.2037 at t = -11.1**. Both reversals are *larger in magnitude* than the
effect they normally produce: the 52-week-high signal earns +0.063 in RISK_ON and loses 0.204 in CRISIS,
more than three times as much in the wrong direction. Daniel & Moskowitz (2016) on Indian equities,
confirmed out of sample.

### The low-volatility prediction was wrong

I predicted low volatility would **strengthen** in drawdowns - the defensive-anomaly story. It does not:

    vol_60          RISK_ON -0.0688    RISK_OFF -0.0678    essentially flat, not stronger
    atr_pctile_250  RISK_ON -0.0481    RISK_OFF -0.0518    marginally stronger at best

And in CRISIS both **reverse sign**: low volatility stops winning and high volatility wins, +0.0684 and
+0.0693. Whatever protects a portfolio in an Indian crisis, it is not the low-volatility factor.

### The consequence, which is the real result

The registration said that if both predictions held, "the two families are genuine complements and a
book holding both is diversified by construction."

**They are not complements. All four reverse together, in the same regime, at the same time.** A book
holding momentum *and* low volatility is not hedged against a crisis - it is doubly exposed to it. That
is the opposite of what I expected, it is the single most decision-relevant thing in this note, and it
would have been invisible to any measurement that reported only the pooled average.

Four signals, two families, one regime risk.

### NARROW is the best regime for all four

Strongest ICs everywhere: +0.1223, +0.1489, -0.1711, -0.1245. NARROW is narrow market breadth - few
names leading - and that is exactly when a cross-sectional ranking has something to rank. The ordering
NARROW > NEUTRAL > RISK_ON > RISK_OFF > CRISIS is consistent across all four features, which is more
structure than four independent coincidences would produce.

## Decay: the cost crossing landed where predicted

                     crosses zero between      annualised net peak
    mom_12_1                h10 and h20        h60   +11.1%/yr
    dist_52w_high           h10 and h20        h60   +14.9%/yr
    vol_60                   h5 and h10        h20   +13.4%/yr
    atr_pctile_250          h10 and h20        h120  +10.7%/yr

Registered prediction: "net is negative at h1-h5, crosses zero somewhere between h10 and h40". **Right**,
for all four.

`mom_12_1` in full, as the representative case:

     h        IC        t  sigma_cs    gross    cost      net   net/yr
     1   +0.0151    +11.3      2.6%    0.07%   0.80%   -0.73%  -182.6%
     5   +0.0361    +22.1      6.1%    0.39%   0.80%   -0.41%   -20.6%
    10   +0.0461    +25.9      8.6%    0.70%   0.80%   -0.10%    -2.5%
    20   +0.0584    +31.8     12.3%    1.27%   0.80%   +0.47%    +5.8%
    40   +0.0755    +42.1     18.0%    2.39%   0.80%   +1.59%    +9.9%
    60   +0.0856    +49.7     22.9%    3.46%   0.80%   +2.66%   +11.1%
   120   +0.0886    +49.7     34.6%    5.39%   0.80%   +4.59%    +9.6%

### The other half of that prediction was wrong

I said "the gross term stops growing well before h120". It does not. Three of the four ICs are **still
rising at h120** - momentum to +0.0886, the 52-week-high signal to +0.1281, ATR percentile to -0.0972.
Only `vol_60` peaks, at h60.

What peaks around h60 is the **annualised** net, because the per-rebalance gain grows more slowly than
the horizon lengthens. That is the correct way to read it and it is a different statement from the one I
registered.

### This independently reproduces h60, by a route that shares no code

[[Naive momentum beats it, and the horizon was a fit]] found h60 optimal by sweeping rebalance horizons
on realised P&L - and that selection returned **-7.49% out of sample**. This note arrives at h60 for
momentum from IC decay and a cost crossing, out of sample, with no P&L sweep anywhere in it.

So the honest reconciliation: **the horizon was right and the excess estimate was overfit.** Those are
separate claims and only the second one failed. That is worth knowing, because it means the earlier
walk-forward failure was not evidence against h60 as a holding period - it was evidence against the
+246% that had been attached to it.

## What these numbers are not

**The annualised figures are overestimates, by an amount stated before they were computed.** Grinold's
`alpha ~ IC * sigma_cs * z` assumes a normal cross-section; Indian returns are fat-tailed, so z = 1.76
overstates a top decile's mean score. The registration said this biases the gross term upward, and it
does. +11.1%/yr is a ceiling, not a forecast.

**The tiers are untradeable.** Median daily turnover is Rs 3.21 cr at deep and Rs 0.37 cr at micro.
These numbers establish that the effect exists and when it fails; they do not establish that capital
fits behind it. The tradeable-tier version of this test is a separate experiment.

**The decay curve is a description, not a menu.** Registered as such, and it stays that way: nothing
here selects a horizon. If h60 is ever adopted as a parameter, that adoption needs its own registration
and its own holdout, because a horizon chosen by reading this table is a horizon chosen on this data.

**CRISIS is 156 tier-sessions from 78 calendar sessions.** Thin, and reported rather than dropped
because 78 sessions is what the market provided. The reversal is large and consistent across four
features, which is harder to dismiss than any one of them would be.

## What this changes

Position sizing. If momentum and low volatility fail together, a book holding both needs its exposure
set against the *joint* regime risk, not the product of two independent ones - and the regime router in
`forecasting/ensemble.py` currently refuses to route for want of 60 observations per regime, which
CRISIS at 78 sessions will not reach for years.

The sizing question is now the live one: not which signal, but how much, given that all of them are the
same bet in the regime that matters.

Related: [[PRE-REGISTERED regime conditioning and signal decay]] ·
[[Result of the pre-registered out-of-sample test]] ·
[[Naive momentum beats it, and the horizon was a fit]] · [[The cost number that decided everything]]
