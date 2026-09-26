---
type: research
tags:
  - research
  - pattern
  - microstructure
  - mistake
date: 2026-09-26
---

# Intraday candles, and the bid-ask bounce that explained them

Candlestick patterns were tested on minute bars - 17.96M of them across 100 names - and
produced the most striking-looking result this project has seen. It was mostly an artifact,
and the control that caught it is the useful part.

## The apparent finding

**Every directional pattern predicted the opposite of its textbook meaning**, with
significance that looked unarguable:

| Pattern | Textbook | Measured edge (30 min) | t |
|---|---|---:|---:|
| hammer | bullish reversal | **-0.0137%** | -6.74 |
| shooting_star | bearish reversal | **+0.0198%** | +6.10 |
| bearish_engulfing | bearish | **+0.0113%** | +5.67 |
| bullish_engulfing | bullish | -0.0005% | -0.64 |

Tens of thousands of observations each, t-statistics of five to nine, and a perfectly
consistent story: at minute resolution the classic reversal patterns reverse *themselves*.

It would have made a good paragraph.

## The mundane explanation, and the control

A hammer closes near its **high**. A trade near the high is more likely to have printed at
the **ask**. The next prints average back toward the mid, so the measured forward return is
negative - with no behaviour involved at all. A shooting star closes near its low, prints
at the bid, and measures positive for the same reason.

That is Roll's (1984) bid-ask bounce, and it predicts exactly the table above, sign for
sign.

**The test:** measure from the *next bar's open* instead of the pattern bar's close. If
the effect is behaviour it survives; if it is bounce it collapses.

| Pattern | From close | From next open | Lost |
|---|---:|---:|---:|
| hammer | -0.0134% | -0.0041% | **69%** |
| shooting_star | +0.0132% | +0.0041% | **69%** |
| bearish_engulfing | +0.0172% | +0.0061% | **65%** |
| bullish_engulfing | -0.0060% | **+0.0053%** | **sign flips** |

Two-thirds of the effect vanishes by shifting the entry one minute. For bullish_engulfing
the sign **inverts** - and a real behavioural effect cannot be reversed by changing which
price you pay.

## What survives, and why it does not matter

About **0.004-0.006%** per pattern, still probably contaminated, because the next bar's
open has a bounce of its own. Against a round trip of 0.71% that is **over 120 times** the
edge - beyond the reach of any execution improvement.

## Why this one is worth keeping

The daily candlestick test rejected all seven patterns and found they select on volatility.
This is a different and sharper lesson: **a result can be highly significant, perfectly
consistent, mechanistically plausible, and still be a measurement artifact.** t = 6 on
60,000 observations is not evidence of anything if the measurement instrument has a known
bias pointing the same way.

The control took one query. It is the same move that caught
[[Beating the median is not an edge]], [[The universe returned 84% a year]] and the
Corwin-Schultz spread: **ask what else would produce this number, then test for it.**

Three of the four artifacts found in this project were caught by a plausibility check, not
by a test. This one was caught by a *designed control*, which is the better instrument, and
the one worth reaching for first now that the pattern is clear.

Related: [[Charts, candles and indicators]] ·
[[Intraday breakouts pay seven times their edge]] ·
[[The cost number that decided everything]] · [[Alpha Validation Firewall]]
