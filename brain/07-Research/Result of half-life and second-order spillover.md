---
type: research
tags:
  - research
  - signal-half-life
  - market-graph
date: 2026-10-04
status: result
---

# Result of half-life and second-order spillover

Predictions committed at `dc8de63` before either study ran.

## A. Signal half-life (127 monthly sessions, 2016-2026)

IC by horizon, signed so positive means "the signal works":

    signal           h=1      5      10     20     40     60     120    peak   half-life
    vol_60 (low)   +0.029 +0.047 +0.059 +0.068 +0.077 +0.086 +0.088   120    > 120
    mom_12_1       +0.006 +0.036 +0.038 +0.051 +0.063 +0.068 +0.078   120    > 120
    dist_52w_high  -0.005 +0.020 +0.032 +0.062 +0.076 +0.094 +0.121   120    > 120
    ret_20d (rev)  +0.032 +0.033 +0.028 +0.008 +0.003 -0.010 -0.024     5    15.8 sessions
    composite      +0.028 +0.060 +0.067 +0.082 +0.098 +0.108 +0.120   120    > 120
    t at h=120:    vol 5.5, mom 7.2, 52w 9.5, composite 8.8

    H1 reversal peaks at h<=10, half-life <=40        PASS   peak 5, half-life 15.8
    H2 momentum / 52w peak >=40, half-life >120       PASS
    H3 vol_60 peaks >=60, no halving                  PASS
    H4 composite peaks between 20 and 60              FAIL   still rising at 120 (+0.120, t 8.8)
    H5 no signal |IC| > 0.02 at h=1                   FAIL   composite +0.028, vol +0.029, reversal +0.032

**What it means.** Every signal except reversal is still accumulating information at 120 sessions, and
the composite's IC at six months is **+0.120 (t 8.8)**, half again the +0.082 at 20 sessions the engine
was evaluated at. The horizon I chose for the engine (20 sessions) sits on the steep part of the curve,
which is exactly where costs are paid most often per unit of information. A book rebalanced every
three to six months pays a fraction of the friction and collects more of the signal. This is the same
shape the earlier sweeps found ("three of four terms still rising at h120") and it is now measured on
the whole liquid universe.

**Reversal is the only fast signal.** Its IC halves in about 16 sessions and turns *negative* by h=60, so
it is a contrarian trade against the momentum signals at long horizons: a book that holds both is long
the same names at 120 days but fighting itself inside a month.

**Caution.** Overlapping forward windows make the IC at long horizons a smoother series than the
t-statistics imply (windows share most of their days at h=120 on a 20-session step). Read the t at 60-120
as optimistic.

## B. Second-order spillover through the dated promoter graph

3,504 `RESULTS` events had at least one live sibling. Effect = sign(reaction) x sibling market-adjusted
return, clustered by entry date (994 dates):

    h       real                     placebo
    1     -0.3 bp   t +0.29          -1.8 bp   t -1.25
    5     -2.4 bp   t -0.77         -15.0 bp   t -1.99
    20   -14.0 bp   t -1.15         -20.7 bp   t -1.18

    S1 positive, < 20 bp, below the bar         PARTLY   below the bar and tiny, but the sign is not positive
    S2 placebo indistinguishable from zero      PASS     (the h=5 placebo is -15 bp at t -2.0, larger than the real effect)
    S3 1-day spillover larger than 20-day       FAIL     nothing at any horizon

**No second-order effect is detectable.** When a promoter-group company reports and its stock reacts,
its siblings do not follow, at 1, 5 or 20 sessions. The promoter graph's cross-sectional signals
(`group_momentum`, `group_stress`) measure something else: the *level* of sibling strength, not
news propagating through the links.

Related: [[PRE-REGISTERED half-life and second-order spillover]] ·
[[Dated promoter membership separates dating from filtering]]
