---
type: research
tags:
  - research
  - pre-registration
  - paper-trading
  - machine-learning
date: 2026-10-07
status: pre-registered
---

# PRE-REGISTERED learned-model and momentum books

Written before either book takes a position. The learned model is the one challenger that passed its held-out test
([[Result of the deep feature library]]: +0.77% a month over the 500 most liquid names in 2022-26, t 2.3; ahead of plain
momentum by +0.29%, t 0.5). Until now it was only recorded as competition picks. These two books trade it - and the
champion it must beat - on paper with rupees, the same way, from 2026-10-07's signals.

## Rules (fixed; a change is a new version)

| | learned-v1 | momentum-v1 |
|---|---|---|
| ranking | the trained LightGBM's score (`data/learned_model_v1.txt`, trained through 2026-08-05, never refit live) | 12-1 momentum |
| universe | the 500 most liquid names on the signal day | the same |
| signal | the top 20 after each close | the same |
| slots | at most 20 open, Rs 10 lakh, 5% a name | the same |
| entry | the next session's close | the same |
| exit | the first close at or below the plan's position stop, else 20 sessions after entry | the same |
| costs | the sized delivery round trip (fees, spread, impact) | the same |
| benchmark | the liquid universe bought and held over the same days | the same |

Each learned-v1 position records **why** it was bought: the model's own split of its score into per-feature
contributions (LightGBM's tree contributions), the five largest for and the three largest against, in plain words.

## What counts

Live paper only (the model was trained on everything to 2026-08, so a replay would be in-sample). Verdict after
**100 sold positions over at least 20 entry days** in each book: learned-v1 is better if its mean net excess over the
universe exceeds momentum-v1's with t >= 2 on the paired entry days. Before that: COLLECTING.

## Predictions

* **B1** Over the first 100 trades neither book's excess over the universe is distinguishable from zero.
* **B2** learned-v1 turns over more names than momentum-v1 (its walk-forward turnover was about twice momentum's).
* **B3** The learned book's most frequent reasons are trend (the 50/200 cross, the 200-day slope, Supertrend) and low
  volatility, as in its training-period importances - not Fibonacci, candles or gaps.
