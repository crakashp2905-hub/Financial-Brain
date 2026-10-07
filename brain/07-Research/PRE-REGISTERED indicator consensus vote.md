---
type: research
tags:
  - research
  - pre-registration
  - features
date: 2026-10-07
status: pre-registered
---

# PRE-REGISTERED indicator consensus vote

Written before it is computed. The owner asked that all the algorithms, ratios and indicators be used to pick trades.
They already are, in one tested way: the learned model ranks on all 161 library features
([[Result of the deep feature library]]). This tests the other natural way - a vote.

## The vote (fixed here)

For each of the 161 features, its **direction** is the sign of its mean rank IC with the 20-session forward return
over **2016-2021 only**. On each date, a name's vote from a feature is its cross-sectional rank (0 to 1, centred at
0.5) times that direction; the **consensus** is the mean vote over all features with a value. Rank by consensus.

Scored exactly like the learned model (the 500 most liquid names, monthly, equal-weight top 20 and top 50, held 20
sessions, sized costs), over **2022-2026**, against the learned model and plain momentum on the same months.

## Pass criterion

Joins the paper books (a "consensus-v1" book) if its 2022-2026 top-20 net excess over the universe is positive with
t >= 2 **and** above the learned model's. Otherwise it is shown only as information (the vote count on charts and
cards).

## Prediction

**C1** It fails: an equal vote of 161 mostly-null features is dominated by the many volatility- and reversal-type
features whose information is in the bottom of the ranking, so its top 20 earns less than the learned model's.
