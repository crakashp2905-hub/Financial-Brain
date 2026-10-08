---
type: research
tags:
  - research
  - result
  - edges
date: 2026-10-08
status: result
---

# Result of edges for every mode

Tests [[PRE-REGISTERED edges for every mode]] and [[PRE-REGISTERED bad news keeps going]]. Bar: net mean positive
with t >= 2 in both 2016-2021 and 2022-2026 (halves of 2024-09 to 2026-10 for minute data). Series saved under
`data/edges/`; code `evaluation/edges.py`.

## The eleven candidates

| Mode | Candidate | 2016-21 net | t | 2022-26 net | t | Verdict |
|---|---|---:|---:|---:|---:|---|
| Index | Z1 turn of the month (per month) | +0.34% | +1.28 | +0.23% | +0.91 | fail |
| Index | Z2 pre-holiday (per event) | -0.16% | -0.99 | -0.21% | -1.49 | fail |
| F&O | F1 long build-up (per week) | +0.04% | +0.21 | -0.17% | -2.00 | fail |
| F&O | F2 short-covering reversal (per week) | +0.22% | +0.63 | -0.04% | -0.47 | fail |
| Swing | S1 no-news reversal (per week) | -0.45% | -4.26 | -0.06% | -0.61 | fail |
| Swing | S2 earnings-reaction drift (per month) | -0.49% | -0.82 | +0.44% | +0.81 | fail |
| Position | P1 residual momentum (per month) | -0.19% | -0.34 | +0.86% | +1.79 | fail |
| Position | P2 vol-managed momentum over cash | +1.49% | +2.30 | +0.79% | +1.22 | fail |
| Position | P3 frog in the pan (per month) | +1.84% | +3.33 | +1.16% | +1.67 | fail |
| Intraday | I2 intraday seasonality (per day, net) | -3.00% | -55 | -3.07% | -67 | fail |
| Intraday | I1 index intraday momentum (per day, net) | -0.040% | -7.53 | -0.040% | -8.06 | fail |

For comparison, plain 12-1 momentum on the same machinery: +1.35% (t 2.15) and +1.06% (t 1.65) a month - it also
misses the bar. P1, P3 and plain momentum all have a 2022-26 net Sharpe of about 0.73-0.82 (total return, 26-28% a
year); they are one phenomenon measured three ways, and none is strong enough on its own to clear t >= 2 twice.

**P2** halved the worst drawdown (48% to 25% in 2016-21; 39% to 21% in 2022-26) but raised the Sharpe only in the
first period (0.98 vs 0.84) and lowered it in the second (0.57 vs 0.73). Not an edge; a risk control.

**I2 is real and untradeable.** Gross, the top-minus-bottom decile earns about 3.6 bp per half-hour slot (t +9.2
and +9.5 in the two halves) - the time-of-day pattern exists. Trading it costs two round trips of ~13.5 bp each
per slot. It is information for *timing* trades already being made, not a strategy.

**I1 has no signal at all**: gross +0.01 bp (2016-21) and -0.04 bp (2022-26) over 2,652 days of Nifty 50
minute bars; the morning move and the last half hour are uncorrelated (-0.02), hit rate 50.5%. **E4 wrong** (it
predicted a positive gross).

**S1** turned a 0.16% gross weekly excess into a 0.43% cost every week (full turnover).

## The information lines that led to a second test

* Losers *with* news: -0.88% (t -5.0) and -0.67% (t -5.2) a week net against the 500.
* Bottom-quintile results reaction: -1.82% (t -2.9) and -1.44% (t -2.6) over 20 sessions net.

Registered as [[PRE-REGISTERED bad news keeps going]] and tested on the names ranked 501-1000 by turnover, which no
test here had touched:

| Test | 2016-21 gross excess | t | 2022-26 | t | Bar (t <= -2 both) |
|---|---:|---:|---:|---:|---|
| N1 weekly news losers, next 5 sessions | -0.40% | -1.62 | -0.32% | -1.87 | **fail** |
| N2 bottom-fifth results reaction, next 20 sessions | -1.29% | -2.59 | -2.06% | -4.73 | **pass** |

**B1 wrong** (N1 points the same way but misses), **B2 wrong** (N2 holds in the later period too, more strongly).

## What changed in the brain

* `decisions/news_filter.py`: a RESULTS reaction (close before to close of the session after, against the
  equal-weighted 500) in the bottom fifth of the previous 30 days' reactions flags the name for 20 sessions. The
  live cut uses the trailing 30 days because the month's full distribution is not known on the day - a stated
  deviation from the test's within-month quintile.
* Three new paper books, the old ones running on as controls: **learned-v2, momentum-v2, v2-v2** - the same rankings
  with flagged names skipped (each skip recorded with its reaction).
* A zoo paper strategy **results_reaction_short**: short the flagged F&O names in futures, long Nifty of equal
  notional, judged on its forward record only.

## By mode, where the edge stands

| Mode | Tested edge now |
|---|---|
| Position (20 sessions) | learned model (+0.77%/month 2022-26, t 2.3); momentum family Sharpe ~0.8 but not t >= 2 twice; plus the rejected-results filter |
| Swing (5-7 sessions) | none - every 5-session idea dies on a full round trip a week |
| Intraday | none on stocks; seasonality is real gross, ~4% of its cost |
| F&O | rich-basis carry (t 2.9); results-reaction short (forward test); open-interest build-up: none |
| Index | none of the calendar effects; Sharpe allocation across strategies beat equal weight every year 2022-26 |

Information, not advice.
