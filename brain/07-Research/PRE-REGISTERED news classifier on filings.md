---
type: research
tags:
  - research
  - pre-registration
  - news
  - nlp
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED news classifier on filings

Written and committed **before** the model is trained.

## Why filings rather than a news API

`news_articles` holds 2 rows and no news-API credential exists in the environment (credentials come only
from the environment, and an account cannot be created here). What the archive does hold is 2.7 million
exchange announcements with a headline, a company, and a publication timestamp - a point-in-time,
stock-tagged news corpus that needs no external service. It is company-sourced, not journalism, so it
answers "which filings matter", not "what did the press say".

## Design

* Universe: top 1,000 ISINs by average turnover. Announcements 2016-04 to 2026-06.
* **Entry**: the first close at or after publication (published before 15:30 IST on a session -> that
  day's close, otherwise the next session's). The reaction up to that close is *not* tradeable and is
  kept out of the target.
* **Targets** (market-adjusted = minus the universe median return over the same window):
  * strength: `|AR3| > q80`, AR3 = the 3-session return after entry, q80 fixed on the training years;
  * direction: `AR3 > 0`.
* Split by time, no shuffling: train 2016-2021, validate 2022, **test 2023-2026, scored once**.
* Company names are stripped from the headline so a model cannot learn "this is Reliance".
* Models: logistic regression on TF-IDF (1-2 grams) + event type + volatility + turnover. The text-free
  control uses the same non-text features, so any gain is attributable to the words.

## Predictions

* **P1 strength: text adds AUC over the text-free control by at least 0.01 on the test years.** I expect
  this to pass: words like "fraud", "default", "order", "merger" carry magnitude information that event
  type does not.
* **P2 strength is mostly volatility.** The text-free control already reaches AUC >= 0.58, i.e. most of
  the predictability is trailing volatility and not news.
* **P3 direction fails.** Top-decile minus bottom-decile mean AR3, date-clustered, will not reach the
  Bonferroni bar (|t| >= 3.70 over the ledger) and the gross spread will be below the ~30 bp round trip.
  A headline classifier with a three-session horizon has no edge I can name.
* **P4 the same-day reaction is where the information is**, and it is not tradeable. Reported for
  context only.

Pass criteria are as written. A failed P3 is reported as a failure, not rescued by changing the horizon,
the decile cut or the universe.
