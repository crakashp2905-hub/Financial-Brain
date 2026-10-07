---
type: research
tags:
  - research
  - pre-registration
  - sharpe
  - allocation
date: 2026-10-07
status: pre-registered
---

# PRE-REGISTERED Sharpe ratio and Sharpe allocation

Written before anything is computed. The owner asked why the brain does not use the Sharpe ratio, and to use it and
run historical paper trades. Until now the Sharpe ratio appeared in a few research tables but decided nothing:
strategies were judged by t-statistics and capital was never split by risk-adjusted return.

## 1. Measure - every strategy, the same way

`evaluation/performance.py`: from a strategy's **monthly net returns** (after costs), annualised Sharpe above a 6.5%
risk-free rate, Sortino, maximum drawdown, Calmar, and two corrections that keep the number honest:

* **Probabilistic Sharpe ratio** (Bailey & Lopez de Prado 2012): the probability that the true Sharpe is above zero,
  allowing for the sample's length, skew and fat tails.
* **Deflated Sharpe ratio** (Bailey & Lopez de Prado 2014): the same, against the Sharpe the best of N trials would show
  by luck, with N = the number of strategy variants this project has tested (the ledger, 300+).

The historical paper record for each strategy is its tested monthly series, 2016-2026, net of the same costs as in its
own result note: momentum, ranking v1, ranking v2, the learned model (2019-26, walk-forward), the eleven technical
rules, PCA stat-arb, pairs (distance), the Nifty schemes, VIX timing, rich-basis carry (by expiry month), and ORB.

## 2. Decide - Sharpe allocation, walk-forward

Each month, split Rs 10 lakh across the strategies in proportion to their **trailing 36-month Sharpe** (only those
above zero; nothing if none is), rebalanced monthly, charged 0.3% on the share of capital that moves. Compared over
**2022-2026** (no weight is set with any later data) with: equal weight across the same strategies; plain momentum
alone; and the best single strategy chosen by hindsight (an upper bound no one could have held).

## Predictions

* **S1** Every equity long-only strategy's net Sharpe above the risk-free rate is under 0.8; the rich-basis carry has
  the highest Sharpe of all (low volatility), the learned model the highest among stock-selection strategies.
* **S2** No strategy's deflated Sharpe ratio exceeds 0.95: after 300+ trials, none is distinguishable from luck at 95%.
* **S3** Sharpe allocation beats equal weight in 2022-2026 by less than 1% a year and does not beat momentum alone by
  more than 2% a year: past Sharpe ratios persist only weakly.
* **S4** The brain uses the Sharpe ratio from now on for reporting (every book, the zoo, the 18:30 report, the
  evidence tiers) whatever S3 says; it **allocates** paper capital by Sharpe (a "sharpe_allocator" book in the zoo)
  only if S3's allocation beats equal weight in 2022-2026 with a positive difference on more than half the years.
