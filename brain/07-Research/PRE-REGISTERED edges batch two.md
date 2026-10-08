---
type: research
tags:
  - research
  - pre-registration
  - edges
date: 2026-10-08
status: pre-registered
---

# PRE-REGISTERED edges batch two

Written on 2026-10-08 after [[Result of edges for every mode]], before anything below is computed. The first batch
showed the binding constraint again: every swing idea had a real gross signal and lost it to a delivery round trip
(25-45 bp) every week, and stock intraday ideas need 15 bp gross a trade that no measured pattern gives. Two routes
are left: **stock futures** (10 bp round trip, and they can short) and the **index** (4 bp).

Same bar as before: net mean positive with t >= 2 in both 2016-2021 and 2022-2026. Costs: 5 bp one way per unit of
weight changed in stock futures, 2 bp one way on Nifty futures. Long-short books are market-neutral (equal gross on
each side) and measured on spot returns (the futures carry nets out across the two legs); cash on the collateral is
not added.

## Swing, in stock futures (F&O names, weekly, held 5 sessions)

* **W1 Short-term reversal, long-short**: long the bottom decile of 5-session return, short the top decile.
* **W2 52-week-high, long-short** (George & Hwang 2004): long the decile closest to its 52-week high, short the
  farthest.

## Position, in stock futures (F&O names, monthly)

* **W3 Momentum long-short**: long the top decile of 12-1 momentum, short the bottom decile.

## Position, delivery (500 most liquid, monthly)

* **E1 Momentum ensemble**: the average cross-sectional rank of 12-1 momentum, residual momentum and nearness to
  the 52-week high, restricted to the continuous-information half (P3's filter); top 20.

## Index (Nifty futures, daily)

* **X1 Overnight**: long from the close to the next open (09:15 first price), flat during the day; 4 bp a day.
* **X2 Opening overreaction fade**: when the 09:15-09:30 move is more than twice its trailing 60-day standard
  deviation, take the opposite position at 09:30 and close at 15:29.

## Predictions

* **C1** W1 passes or comes close: the reversal that died on delivery costs survives at 10 bp with a short leg.
* **C2** W2 and W3 have positive means in both periods; at most one passes.
* **C3** E1 has a higher 2022-26 Sharpe than any of its parts, and still fails t >= 2 in one period.
* **C4** X1 and X2 fail.

Each that passes becomes a paper strategy; each that fails is paper-traded in the zoo with its verdict beside it, as
the owner asked that everything built be tried on paper.

Information, not advice.
