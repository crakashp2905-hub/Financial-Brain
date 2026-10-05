---
type: research
tags:
  - research
  - pre-registration
  - paper-trading
  - swing
date: 2026-10-05
status: pre-registered
---

# PRE-REGISTERED swing paper book (swing-v1)

Written before it is run, at the owner's request to see fifty swing candidates a day with their paper P&L instead
of five position names. Same machinery as [[PRE-REGISTERED suggestion paper book v1]]; what differs:

1. **Signal.** After each session's close D, the liquid cross-section ranked by the decile model's **7-session**
   expected excess return (the composite decile's measured 20-session mean x the corrected horizon scale 0.453),
   ties broken by the composite score. Not by cost: book-v1's replay showed that breaking ties by cost made the
   book a large-cap bet (it lost 3.1% a trade to the universe through the 2023-26 small-cap rally). The top 50.
2. **Slots.** Rs 10 lakh, at most 50 open, so 2% (about Rs 20,000) a name at most.
3. **Size and stop.** The plan's swing style: 0.5% of capital at risk, stop at 2 sigma over 7 sessions or the ATR
   equivalent if wider, capped at 2% weight and 10% of daily turnover.
4. **Entry / exit.** The close of D+1; the first close at or below the stop, else the close 7 sessions after entry.
5. **Costs.** The sized delivery round trip, including the Rs 15.34 DP charge per sale - on a Rs 20,000 position
   that alone is 7.7 bp. Benchmark: the liquid universe bought and held over the same days.

## Predictions

The swing study ([[Result of swing setups]]) found every 5-10 session setup losing 0.2-0.6% a trade net, and the
model's own 7-session expectation for the best deciles is about +0.19% against a round trip of about 0.3-0.4%.

* **S1** Over a replay long enough to judge (2023 onwards), the mean net excess per trade is **negative**.
* **S2** The win rate (net > 0) is under 55%.
* **S3** One week of trades - which is what was asked for first - decides nothing: with 50 names that share one
  market, a week is one observation. It is shown as a demonstration of the mechanics, not as a result.

Intraday trades for the same week are the existing live-v1 rules (ORB and GAP), replayed on Kite minute bars,
unchanged.
