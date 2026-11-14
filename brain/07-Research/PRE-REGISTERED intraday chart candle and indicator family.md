---
type: pre-registration
tags: [research, pre-registered, intraday, chart-patterns, candles, indicators, family]
date: 2026-11-11
status: registered before any result; nothing here has been computed
---

# PRE-REGISTERED intraday chart candle and indicator family

One trial, one family: forty candidate intraday strategies for a `live-v2`, tested once on the 2016-2026 minute
history. Authority: [[ADR-0004 one evidence standard]], amendment 5 (2026Q4 budget 13 to 14), owner decision on
2026-11-11. The candidates come from the two lists the owner reviewed on 2026-11-11: twenty variants and reuses of
existing algorithms (`L1`-`L20`) and twenty chart, candle and indicator strategies (`P1`-`P20`). Sources for the
definitions: [[Source notes - Nison Japanese Candlestick Charting Techniques]], [[Source notes - Bulkowski Encyclopedia
of Chart Patterns]], [[Chart patterns and candles - what is testable and how]]. This note is committed before any
result exists; a change after a result is a new hypothesis with a new name, never an edit.

## What is expected

The honest prior is a null. Daily candle and chart-setup families failed (K7), the 9-rule intraday and swing family
failed (SPA p 0.515), ORB lost 15-21 bp over 251,000 backtest trades, and minute-candle effects were bid-ask bounce.
The two places an effect could survive are filters that remove bad trades from a strategy that is gross-positive, and
confirmed continuation breakouts that overlap with momentum. Forty tries will produce a few lucky-looking rules; the
gates below, the family tests and the random-entry null exist to say which are luck.

## Data, universe, bars

* Source: `data/minute_history.duckdb` (`minute_bars`), sessions 2016-01-04 to 2026-10-08, 09:15-15:29 IST, read-only.
  Index context from `data/edges/nifty50_minute.parquet`; daily regime from `market_regime` (version `v2`), previous
  session's label only.
* Names: the F&O stock names with minute history (as in the intraday and swing family). Each calendar quarter the
  tradable universe is the **100** names with the highest median daily traded value (sum of close x volume per
  session) over the previous 60 sessions, point in time. 2016Q1 has no history and does not trade. Survivor bias is
  stated: the minute history holds currently listed names only.
* Bars: 5-minute bars from 09:15 (75 a session), 15-minute bars as three 5-minute bars (25 a session). A bar is known
  only at its end. Indicators on 15-minute bars run on the continuous series across sessions; everything else is
  session-local. Each rule names its bar size.
* Daily context from the same minute data: previous close `PC`, previous high `PH`, previous low `PL`, `gap =
  open / PC - 1`, `SMA20` = 20-session mean of daily closes, `OR_hi`/`OR_lo` = high/low of 09:15-09:29 (three 5-minute
  bars), `VWAP` = cumulative (high+low+close)/3 x volume over cumulative volume from 09:15, `ATR5` = mean true range of
  the last 14 five-minute bars of the session (needs 14 bars), `ATR15` = the same on fifteen-minute bars (continuous).

## Trade conventions (the same for every rule unless the rule says otherwise)

1. One trade per rule, symbol and session: the first signal.
2. A signal bar completes between 09:30 and 14:30 IST. Entry is at the **open of the next 5-minute bar** (never the
   signal bar). Long or short as the rule says; shorts are taken as stock futures.
3. Stop as the rule says, but never closer than **0.25%** of the entry price. Target = entry +/- 2 x |entry - stop|
   unless the rule says no target. If a bar reaches both, the stop is taken first; a bar that opens beyond a level exits
   at its open. Time exit at the close of the 15:10-15:15 bar.
4. Net return of a trade = direction x (exit / entry - 1) - **0.0010** (10 bp round trip: the live schedule is about
   8 bp; 2 bp of margin). Gross and a 20 bp round-trip sensitivity are reported and not judged.
5. Judged series: per rule, the **mean net return per trade in each calendar month**, months with at least 10 trades.
   Statistic: t of that monthly series in 2016-2021 and in 2022-2026 (`standard.replication`, split 2022-01-01).

## The candidates

Chart-pattern rules use the existing causal detectors (`financial_brain.patterns`) **at their default parameters, once**,
on the session's 5-minute bars (15-minute where stated). A formation is a signal at the bar where it is CONFIRMED with a
direction (`confirmed_index`, `confirmed_direction`). Natural stop = the extreme of the formation's own bars
(`start_index` to the signal bar) on the far side of the trade. No tolerance is tuned.

**Family L: existing algorithms and filters** (control `L0` = plain live-v1 ORB and `G0` = live-v1 GAP are reported, are
not counted and are not judged)

| id | rule |
|---|---|
| L1 | ORB with 09:30 side filter. ORB: first 5-minute close beyond `OR_hi` (long) or `OR_lo` (short) between 09:30 and 11:00; stop the opposite end; 2R. Long only if the stock opened above `PC` and Nifty's 09:30 open is above its 09:15 open; short only on the reverse |
| L2 | ORB in trend direction only: long only if Nifty `PC` > Nifty `SMA20` and stock `PC` > stock `SMA20`; short only if both are below |
| L3 | ORB only when the opening range width is between 0.5% and 2.0% of the open |
| L4 | ORB cost gate: only when the opening range width is at least 0.4% of the open |
| L5 | ORB exits: no target; trailing stop 1 range-width behind the running extreme; flat at 13:00 |
| L6 | GAP fade, large gaps: `abs(gap) >= 2.5%`; entry when a 5-minute close returns through the day's open (from 09:20); stop = the day's extreme so far; target `PC` |
| L7 | Gap-and-go: `abs(gap) >= 1%`; ORB signal in the gap's direction only; stop the opposite end; 2R |
| L8 | Opening-range spring and upthrust: after 09:30 a 5-minute bar trades beyond `OR_hi` (`OR_lo`) and closes back inside; trade back (short for the high); stop = that bar's extreme; 2R |
| L9 | Prior-day level retest: first 5-minute close beyond `PH` (`PL`) after 09:30, then within 12 bars a bar whose low (high) touches within 0.05% of the level and closes beyond it; trade the break direction; stop = retest bar's extreme; 2R |
| L10 | Gap-window continuation: `abs(gap) >= 0.5%`, no bar through 10:15 trades back to `PC`, and the 10:10-10:15 close is on the gap side of the day's open; trade the gap direction at 10:15; stop `PC`; 2R |
| L11 | Opening-drive exhaustion fade: by 09:45 the stock is at least 1.5% from the open at a 09:15-09:45 extreme; after 09:45 the first 5-minute bar that fails to make a new extreme and closes beyond the prior bar's far side starts the fade; stop = the drive extreme; 2R |
| L12 | Stock VWAP pullback: stock `PC` > `SMA20` (long) or < (short); after 10:00 a bar whose low (high) touches `VWAP` and closes on the trend side of it; stop = that bar's extreme; 2R |
| L13 | Relative strength vs Nifty: at 10:15, stock return since open minus Nifty return since open at least +1.0% (long) or at most -1.0% (short); stop 3 x `ATR5`; 2R |
| L14 | Open-interest build-up: **UNTESTABLE** (no minute-aligned open-interest history; the quote archive holds weeks) |
| L15 | Regime switch, from the previous session's `market_regime` label: RISK_ON runs ORB long only, RISK_OFF runs ORB short only, NEUTRAL or NARROW runs `L8`, CRISIS trades nothing |
| L16 | Machine-learning skip filter on ORB: **UNTESTABLE** here (needs a model fitted on the same data; it belongs in a later registration with nested out-of-time fitting) |
| L17 | Results-day reaction: **UNTESTABLE** here (announcement times are not aligned to minute bars) |
| L18 | Time-of-day window: ORB signals taken only between 09:30 and 10:15 |
| L19 | Option overlay on ORB: **UNTESTABLE** (no option quote history) |
| L20 | Order-book imbalance: **UNTESTABLE** (depth archive holds weeks; licence rights unclear) |

**Family P: chart patterns, candles, indicators**

| id | rule |
|---|---|
| P1 | Rectangle confirmed break (`rectangle`, 5-minute) |
| P2 | Triangle confirmed break (`triangles`: ascending, descending, symmetrical; 5-minute) |
| P3 | Flag or pennant confirmed break (`flags`, `pennant`; 5-minute) |
| P4 | Double top or bottom confirmed (`double_extrema`; 5-minute); entry after confirmation, never at the second extreme |
| P5 | Head-and-shoulders top or bottom confirmed (`head_shoulders`; 5-minute) |
| P6 | Cup with handle confirmed (`cup_handle`; 5-minute) |
| P7 | Channel confirmed break (`channels`; 5-minute); the break direction is traded (the bounce half of the idea is not tested) |
| P8 | Failed gap: `abs(gap) >= 0.5%` and a 5-minute close back through `PC` within the first 60 minutes; trade away from the gap; stop = the day's extreme so far; 2R |
| P9 | Throwback entry on P1, P2, P3 signals: after confirmation, within 12 bars the first bar whose low (high) is at least 0.5 x `ATR5` back from the confirmation close and whose close is still beyond the confirmation bar's far side; stop = the lowest low (highest high) since confirmation; 2R |
| P10 | Engulfing or dark-cloud cover at the opening-range edge (15-minute): bullish engulfing (bearish prior body fully engulfed) with its low within 0.2% of `OR_lo`, then the next 15-minute bar closes above the engulfing close; mirror for bearish engulfing or dark cloud at `OR_hi`; stop = the engulfing bar's extreme; 2R |
| P11 | Hammer or shooting star at a level (15-minute): hammer = lower shadow at least 2 x body and upper shadow at most 10% of range; bar low within 0.15% of `VWAP` or `PL` (long); shooting star mirrored at `VWAP` or `PH` (short); stop beyond the shadow; 2R |
| P12 | Morning or evening star after the first hour (15-minute, bars after 10:15): first bar a body of at least 1 x `ATR15` against the move, second a body of at most 30% of the first, third closing beyond the first's midpoint; morning star needs price at 10:15 at least 0.5% below the open, evening star above; stop = the extreme of the three bars; 2R |
| P13 | Rising or falling three methods (`three_methods`, 15-minute) |
| P14 | Spring or upthrust with a candle trigger (15-minute): after 10:15 a bar trades beyond the 09:15-10:14 range extreme, closes back inside, and is a hammer (spring, long) or shooting star (upthrust, short); stop = the bar's extreme; 2R |
| P15 | Bollinger squeeze then breakout (15-minute): 20-bar band width at or below its 10th percentile of the previous 120 bars, then a close beyond a band; stop = the 20-bar mean; 2R |
| P16 | Donchian breakout with ADX (15-minute): close beyond the previous 20 bars' extreme and ADX(14) at least 25; stop = the Donchian midpoint; 2R (Ichimoku is not used) |
| P17 | RSI(14) divergence at a level (15-minute): price makes a lower low (higher high) than the previous 2-2 pivot while RSI makes a higher low (lower high), and the pivot is within 0.15% of `OR_lo`/`PL` (long) or `OR_hi`/`PH` (short); stop = the pivot extreme; 2R |
| P18 | EMA ribbon pullback (15-minute): EMA 8 > 13 > 21 > 34 > 55 (long) or reverse (short); a bar whose low (high) touches EMA 21 and closes beyond EMA 8; stop = that bar's extreme; 2R |
| P19 | Volume-spike breakout (5-minute): close beyond the previous 12 bars' extreme after 10:15 with volume at least 1.5 x the mean of the same slot over the previous 20 sessions; stop = the 12-bar opposite extreme; 2R |
| P20 | Confluence score (15-minute bar ends): count of agreeing items among {a confirmed P1-P7 pattern in that direction, a P10-P14 candle, close on the trend side of `VWAP` and EMA 21, within 0.2% of `PH`/`PL`/`OR` edge on the right side, volume at least 1.5 x slot mean, Nifty above (below) its 09:15 open}; trade at 3 or more; stop 3 x `ATR5`; 2R |

Family size for multiplicity is **forty**; thirty-five are evaluated and five are recorded UNTESTABLE (L14, L16, L17,
L19, L20). `L0` and `G0` are controls: they are reported beside the family and are not part of it.

## Gates, family tests and verdicts

* **G1** this note, audited from git. **G2** the 10 bp round trip above. **G3** `standard.replication` on the monthly
  net series: |t| >= 2 with a positive sign in both 2016-2021 and 2022-2026. A significantly **negative** rule is not a
  candidate; its inverse would be a new hypothesis.
* **G4** random-entry null, 200 draws, 95th percentile of `min(t_a, t_b)`: each real trade is replaced by a trade in
  the same symbol on a random other session of the same quarter, the same entry time slot, the same direction, and the
  same stop and target distances in percent, with the same exit rules. The rule's `min(t_a, t_b)` must beat it.
* **Family:** White's Reality Check and Hansen's SPA on the monthly series of the evaluated rules, and the probability
  of backtest overfitting (16 blocks). The Bonferroni |t| bar for 35 rules is reported beside every result as
  information, not as the gate.
* **Verdict:** G1-G4 pass for a rule -> `CANDIDATE` for forward paper at zero real capital; no rule becomes a live
  strategy on this trial alone, and G5 (12 months out of sample) is required before any claim. A rule that clears G3 but
  not G4, or clears only one half, is a lead, not a result. The whole family is reported rule by rule with both halves
  side by side, including the failures.

## Stated limits

Survivor-only universe; 5- and 15-minute bar aggregation hides intrabar order; stop-before-target on a bar that
reaches both is conservative; the random-entry null uses the same exit rules but not the same intraday volatility
state; gap-through exits at the open assume fills there. Pattern detectors were built and unit-tested for causality on
other data and are not tuned here. Paper P&L never tunes a rule; a candidate that survives is frozen as written.

## Clarifications written before any result (2026-11-11, after the code design, before any run)

These fix wording the table left open; no result existed when they were written and none of them uses data.

* `ATR5` / `ATR15` use all bars available when fewer than 14 exist, with a minimum of 8; a rule needing it skips
  the session before that. (Resolves `L13` at 10:15, `P9`, `P20`.)
* A name trades in a session only if it had a live near-month future that session (the basis-contract calendar),
  because shorts are taken as stock futures.
* Windows are by bar **start** time: "after 10:00" means bars starting 10:00 or later; a signal bar is allowed when it
  completes between 09:30 and 14:30.
* `P2` includes the ascending, descending and symmetrical triangles only; the wedge formations the same detector
  returns are not traded. `P3` uses every `flag*` and `pennant*` formation.
* `P20` items: (1) a confirmed `P1`-`P7` formation in that direction within the previous 12 five-minute bars; (2) a
  bullish engulfing, hammer or morning star (short: bearish engulfing, dark-cloud cover, shooting star or evening star)
  completing at that 15-minute bar, with no level condition; (3) close on the trend side of `VWAP` and EMA 21; (4) the
  bar's low (high) within 0.2% of `PL` or `OR_lo` (`PH` or `OR_hi`); (5) volume at least 1.5 x the mean of the same
  three five-minute slots over the previous 20 sessions; (6) Nifty's close above (below) its 09:15 open. If both
  directions reach 3, nothing is traded.
* `P17`: a pivot is a bar whose low (high) is strictly below (above) the two bars on each side, known two bars later;
  the previous pivot of the same kind must lie within the previous 40 fifteen-minute bars.
* `L5` trail: the stop is raised (lowered) each bar to the running extreme minus (plus) one opening-range width.
