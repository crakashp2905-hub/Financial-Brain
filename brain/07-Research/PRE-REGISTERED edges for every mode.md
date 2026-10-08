---
type: research
tags:
  - research
  - pre-registration
  - edges
date: 2026-10-08
status: pre-registered
---

# PRE-REGISTERED edges for every mode

Written on 2026-10-08 before anything below is computed. The owner set today's goal: develop an edge for every mode
the brain trades. The diagnosis of 2026-10-07 ([[2026-10-07 why the paper trades lose]]) says where to look: each
mode needs a source of return that is larger than its own cost, so every candidate here is either on a cheaper
instrument (index futures) or uses information the brain has never tested (announcement timing, futures open
interest, the time of day, residual returns). Each idea comes from published work; none was chosen by looking at
this data.

## The bar (fixed here)

A candidate **passes** only if its mean net return (after the costs below) is positive with **t >= 2 in both** the
discovery period **2016-2021** and the test period **2022-2026** (t across independent periods - days, weeks or
months as stated). Under the null that has probability about 0.001 per candidate, about 0.01 for all eleven. Where
only minute data from 2024-09 exists, the two periods are the two halves of the sample, split at 2025-10-01.
Deflated Sharpe against the project's trial count is reported for every candidate and decides nothing.

Costs, round trip: Nifty index futures **4 bp** (STT 0.02% on the sell side plus exchange charges and one tick of
slippage); stock futures **10 bp**; intraday stocks the paper engine's schedule (~8.5 bp) plus 5 bp impact; delivery
stocks the sized schedule the books use (`decisions/net_alpha`).

A candidate that passes becomes a paper strategy (a new version; nothing existing changes). One that fails is
recorded and not used. No candidate is altered after its numbers are seen; a variant would be a new registration.

## Intraday

* **I1 Index intraday momentum** (Gao, Han, Li & Zhou 2018). On each day, the Nifty 50's return from the previous
  close to 09:45 sets the direction; the position is taken at 15:00 and closed at 15:29 in Nifty futures. Data: the
  Nifty 50 index minute bars from Kite, 2016-2026 (NIFTYBEES minute bars 2024-26 if the index history is not
  available - then the half-split periods). Unit: day.
* **I2 Intraday seasonality** (Heston, Korajczyk & Sadka 2010). For the 300 names with minute bars, a stock's mean
  return in the same half-hour slot over the previous 20 sessions; each slot, long the top decile and short the
  bottom decile, equal weight, held for that half hour. Intraday stock costs. Unit: day (all slots summed). Half-split.

## Swing (5 sessions)

* **S1 No-news reversal** (Chan 2003). Among the 500 most liquid names, weekly: the bottom decile of 5-session
  market-adjusted return **with no announcement** of type RESULTS, BOARD_OUTCOME, PRESS_RELEASE,
  SUBSTANTIAL_ACQUISITION, MANAGEMENT_CHANGE or INVESTOR_PRESENTATION in those 5 sessions; bought at the close, held 5
  sessions; net excess over the 500. Unit: week.
* **S2 Earnings-announcement return drift** (Brandt et al. 2008). For each RESULTS announcement, the market-adjusted
  return from the close before it to the close of the session after it; names in the top quintile of that reaction
  (within the month) bought at the following close and held 20 sessions; net excess over the 500. This is the
  *reaction*, not the earnings surprise tested in [[Result of setup analogues and earnings reaction]]. Unit: month.

## Position (20 sessions, monthly)

* **P1 Residual momentum** (Blitz, Huij & Martens 2011). 12-1 month return of each name's residual against the
  market (beta from the trailing 36 months), divided by the residual volatility; top 20 of the 500 most liquid,
  held 20 sessions; net excess over the 500. Unit: month.
* **P2 Volatility-managed momentum** (Barroso & Santa-Clara 2015). The momentum top-20 book scaled each month by
  12% / its trailing 6-month realised volatility (capped at 1.5x, the rest in cash at 6.5%). Passes if its Sharpe
  exceeds the unmanaged book's in both periods **and** its net monthly return beats cash with t >= 2 in both.
* **P3 Frog in the pan** (Da, Gurun & Warachka 2014). Momentum top 20 chosen only among names in the lower half of
  information discreteness (sign(12-1 return) x (share of down days - share of up days)); net excess over the 500.
  Unit: month.

## F&O (stock futures, 5 sessions)

* **F1 Long build-up**. Among F&O names, weekly: price up over 5 sessions and futures open interest change in the top
  quintile; long the futures, held 5 sessions; net excess over the F&O names' mean. Unit: week.
* **F2 Short-covering reversal**. Price up over 5 sessions with open interest falling (bottom quintile of OI change);
  short the futures, held 5 sessions; net return of the short relative to the F&O mean. Unit: week.

## Index calendar (Nifty futures)

* **Z1 Turn of the month** (Ariel 1987; documented for India). Long Nifty futures from the close two sessions before
  month end to the close of the third session of the new month, cash otherwise; net return over cash. Unit: month.
* **Z2 Pre-holiday**. Long Nifty futures from the close before each exchange holiday (a weekday with no session) to
  the next close; net over cash. Unit: event. (Few events: the weakest test here.)

## Predictions

* **E1** At most three of the eleven pass.
* **E2** I2 is positive gross but fails on cost (stock round trips every half hour).
* **E3** P1 and P3 have higher Sharpe than plain momentum in 2022-26; at least one passes.
* **E4** I1 is positive gross in both periods; whether it clears 4 bp is a coin flip.
* **E5** S2 and Z2 fail.
* **E6** P2 raises Sharpe in both periods.

Information, not advice.
