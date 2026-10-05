---
type: research
tags:
  - research
  - pre-registration
  - analogues
  - earnings
date: 2026-10-05
status: pre-registered
---

# PRE-REGISTERED setup analogues and earnings reaction

Written before either is computed.

## A. Setup analogues (a tool, with one testable claim)

For a name on a date, the k = 200 most similar past (name, date) situations by eight cross-sectionally ranked
features (1-, 5- and 20-day return, 12-1 momentum, distance from the 52-week high, 60-day volatility, RSI,
turnover against its 20-day average), drawn only from windows that had closed before the date. Outcome: excess
return over the cross-sectional mean at 1, 3, 5 and 20 sessions, and the best and worst points over 5 sessions
(MFE/MAE). The tool reports the analogues' distribution next to the whole pool's, so "68% win rate" is always
shown against what any random situation did.

**A1** The analogue mean predicts the next 5 sessions' excess return out of sample with a rank IC no better than
the momentum signal alone (|difference| < 0.01): nearest neighbours on these features rediscover momentum and
reversal, nothing new.

## B. Earnings reaction by surprise (exchange XBRL, quarters ended Dec 2018 to Dec 2024)

For each results filing: the standardised unexpected earnings at the moment of filing (this quarter's EPS change
on the same quarter a year earlier, over the standard deviation of the previous changes), the day-0 reaction (the
previous close to the first close after dissemination) and the drift 5 and 20 sessions after that close, all
market-adjusted (minus the universe mean). Quintiles of SUE.

* **E1** day-0 reaction rises with SUE: the top-minus-bottom quintile reaction is above +2%.
* **E2** the 20-session drift after the reaction, top minus bottom quintile, is positive but under 1% and does not
  clear the running Bonferroni bar: the market prices most of a surprise on the day.
* **E3** the 5-session drift is smaller than the day-0 reaction by a factor of at least 3.
