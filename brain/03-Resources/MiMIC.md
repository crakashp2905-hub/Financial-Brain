---
type: resource
verdict: STUDY - partly adopted
tags:
  - resource
  - paper
  - india
---

# MiMIC

**arXiv 2504.09257** · *MiMIC: Multi-Modal Indian Earnings Calls Dataset* · 1,042
instances from 768 transcripts and 833 presentations, 133 companies, 2019-2024.

## Taken
- Earnings calls **and** investor presentations carry signal in this market, and
  presentations - usually discarded - are worth keeping. That argument built
  [[Earnings calls]].
- The **cascaded** design: feed a model's *probability* into the next stage as a feature
  rather than pouring raw embeddings in. This is the same instinct as [[Typed decisions]],
  and the paper reports the cascade beating direct embedding incorporation.
- `nomic-embed-text` as a local embedding model, which we already had pulled.

## Not taken
Its headline result is a next-day **price regression** at MAE 104.8 and **MAPE 0.334** -
a 33% mean error - reported without transaction costs, without capacity, and without a
naive baseline such as "tomorrow's open equals today's close". Nothing in that is a
tradeable edge, and reproducing the target would add a confident-sounding number to a
system whose whole discipline is refusing those.

Related: [[Earnings calls]] · [[Alpha Validation Firewall]] · [[MOC Research]]
