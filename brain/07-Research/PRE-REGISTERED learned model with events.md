---
type: research
tags:
  - research
  - pre-registration
  - edges
  - learned-model
date: 2026-10-08
status: pre-registered
---

# PRE-REGISTERED learned model with events

Written on 2026-10-08 before anything below is computed. Of everything tested, the learned model is the strongest
position-mode edge (+0.77% a month net top 20, 2022-26, t 2.3 - [[Result of the deep feature library]]), and its
161 features are all price and volume. Today found one event effect that replicates on unseen names (the rejected
results reaction - [[Result of edges for every mode]]). Two tests.

## L1 Learned model v2: the same model with event features

Exactly the protocol of the deep library: the stored panel (`data/feature_library_panel.parquet`, every 5th session,
the 500 most liquid), features ranked per date, LightGBM with the same parameters, walk-forward by calendar year
2019-2026 with a 30-day embargo, target the 20-session forward return rank; books every 4th sampled date, top 20,
net of the same round trips. v1 is re-run in the same script as the control. v2 adds, per name and date, only what
was known at that close:

* the latest RESULTS reaction (market-adjusted, close before to close after) in the last 60 sessions, and the
  sessions since it;
* material-news counts over 5 and 20 sessions and sessions since the last (from the `features` table:
  `news_5d`, `news_20d`, `days_since_news`, `dealing_60d`, `adverse_60d`);
* for F&O names, the 5- and 20-session change in futures open interest (missing otherwise).

**Pass (becomes a challenger paper book `learned-v3`)**: v2's 2022-2026 top-20 net excess has t >= 2 and its mean
exceeds v1's by at least 0.2% a month. **Replaces v1** only if the monthly difference v2 - v1 itself has t >= 2.

## L2 Learned model, long-short in stock futures

The walk-forward v1 predictions (from L1's control run), restricted to F&O names on each book date: long the top
decile, short the bottom decile, equal gross, held 20 sessions, 5 bp one way per unit of weight changed. Pass: net
mean positive with t >= 2 in both 2019-2021 and 2022-2026 (the walk-forward starts in 2019).

## Predictions

* **M1** L1: v2 beats v1 by under 0.2% a month - the event features are sparse (results four times a year) and
  the model already sees the price reaction through the return features.
* **M2** L2 fails: the short side of a cross-sectional model in large liquid names earns little.

Information, not advice.
