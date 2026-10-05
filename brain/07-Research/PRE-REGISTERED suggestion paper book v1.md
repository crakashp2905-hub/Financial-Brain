---
type: research
tags:
  - research
  - pre-registration
  - paper-trading
  - suggestions
date: 2026-10-05
status: pre-registered
---

# PRE-REGISTERED suggestion paper book (book-v1)

Written before the book is replayed or run. The point: the terminal shows five names a day; this book **buys them
on paper and sells them by the terminal's own exit rules**, so every suggestion has a recorded outcome - the losers
as well as the winners. A track record that showed only the successes would manufacture confidence, so the book
shows every trade it took, and the page puts the worst trades next to the best.

## Rules (fixed; a change is book-v2, written down first)

1. **Signal.** After each session's close D, the terminal ranking: `scanners.scan` on the liquid cross-section,
   one row per name, sorted by expected net return. The top 5, whether or not they clear costs (that is what the
   terminal shows).
2. **Slots.** Capital Rs 10 lakh, at most **10** positions open or waiting to open (the plan caps one name at
   10%). Signals fill free slots in rank order; a name already held is skipped. No slot, no trade.
3. **Size.** The plan's shares on day D (risk budget over stop distance, capped by weight and liquidity).
4. **Entry.** The close of the next session D+1. The archive has closing prices only; the decision uses D's close,
   so D+1's close is the first price the book can honestly fill at.
5. **Exit.** The first close at or below the stop shown on the terminal: sold at that close. Otherwise sold at
   the close `horizon` sessions after entry (the opportunity's horizon, 60 for the names that top the board). No
   profit target (the exit study found targets add nothing).
6. **Costs.** The sized delivery round trip (statutory charges, DP charge, spread and square-root impact) is
   subtracted from every trade. Tax is not subtracted here; the cost calculator shows it.
7. **Benchmark.** The equal-weighted liquid universe (turnover >= Rs 1 crore a day) over the same days. "Excess"
   is the trade's net return minus that.

## What counts

* **Replay** (2023 onwards, same rules) gives a record today, but the expected-return model behind the ranking
  was measured on 2016-2026, so the replay is **in-sample** and is labelled so everywhere. It is a demonstration,
  not evidence.
* **Live** begins with signals of 2026-10-05. Verdict only after **100 closed live trades over at least 20
  entry days**: the book is working if the mean net excess per trade is positive with t >= 2, one observation
  per entry day. Before that it is COLLECTING, whatever the P&L says.

## Predictions

* **P1** In the replay, the mean net excess per trade is positive but under +1%, and its t is under 2: the
  ranking's edge (about +0.55% gross over 60 sessions against 0.26% cost) is small relative to a single stock's
  60-day noise.
* **P2** The win rate against the universe is between 45% and 60%. A book of good suggestions still loses on
  close to half its trades; the page must make that visible.
* **P3** Stops close fewer than 20% of trades (the position stop is 2.5 sigma over 20 sessions).
