---
type: research
tags:
  - research
  - result
  - signal-half-life
  - intraday
date: 2026-10-05
status: result
---

# Result of intraday half-life

Pre-registration: [[PRE-REGISTERED intraday half-life]]. Kite minute bars, ~300 liquid names, 499 sessions
(2024-09 to 2026-10). Daily signals as of the previous session's close; rank IC against the return from the 09:30
price to each offset; one IC per session, t across sessions. Signed so positive means "the signal works" (low
volatility and 20-day reversal are sign-flipped). Running Bonferroni bar 3.79.

    signal            5m      15m     30m     60m     120m    15:15
    mom_12_1        +0.008  +0.004  -0.002  -0.004  -0.004  +0.004   (t at 15:15 +0.8)
    dist_52w_high   +0.034  +0.029  +0.018  +0.019  +0.018  +0.031   (t +4.3; +4.7 at 5m)
    vol_60 (low)    +0.021  +0.035  +0.036  +0.037  +0.041  +0.058   (t +7.2)
    ret_20d (rev)   -0.015  -0.005  -0.000  -0.002  -0.005  +0.001   (t +0.3; -2.6 at 5m)
    composite       +0.027  +0.031  +0.025  +0.025  +0.023  +0.046   (t +6.9)

    H1 no |IC| > 0.01 at 5-60 minutes             FAIL   52w-high, low-vol and composite all 0.02-0.04
    H2 reversal has the largest same-day IC       FAIL   reversal is the only one near zero (wrong sign at 5m)
    H3 IC at 15:15 below the daily 1-session IC   FAIL   holds for momentum and reversal; low-vol (0.058 vs
                                                         0.029), 52w-high (0.031 vs -0.005) and the composite
                                                         (0.046 vs 0.028) are higher intraday

## Reading it

All three predictions were wrong, and in the same direction: the slow signals are **not** noise inside the day.
From 09:30 to 15:15 the names the composite likes beat the names it dislikes with IC +0.046 (t 6.9), and the
low-volatility and nearness-to-high signals carry it. Momentum alone and reversal do not.

Three cautions before anything is built on it:

1. **Different samples.** The daily 1-session IC is 2016-2026, monthly sessions, the broad universe; this is
   2024-2026, every session, 300 liquid names. Low volatility had a particularly good 2024-26 (the market's
   drawdown from late 2024 favoured defensive names). The comparison in H3 is not like for like.
2. **Different windows.** 09:30 to 15:15 leaves out the overnight gap and the first 15 minutes; close to close
   includes both. A signal can earn its return in the day session and give part of it back overnight.
3. **IC is not money.** An IC of 0.05 across 300 names is a spread of a few basis points between deciles in a
   day; the intraday round trip costs 5-25 bp. This is not an intraday strategy.

What it does change: for a delivery or swing position the brain already wants, **there is no case for waiting
inside the day for a better price**. The day session tends to move the way the signal points, so the execution note
on the terminal says to enter in the morning rather than wait for a dip. The intraday paper
strategies (ORB, GAP) are unchanged: live-v1 is locked, and a composite-tilted ORB would be a new pre-registered
version, if the owner asks for one.
