---
type: concept
tags:
  - concept
  - validation
---

# The unanswered question

Everything built so far answers **"what happened, and is it true?"** Nothing yet answers
**"does acting on this make money?"**

The system can: classify a filing, read the number out of its PDF, say how unusual it is
for that company, recover the news the filing quotes, refuse a model that is not
calibrated, and cite every claim to the bytes it came from. None of that is an edge. A
perfectly accurate description of the past is not a reason to buy anything.

What would answer it, in order:

1. **Decisions that reach paper.** The [[C17 Decision record and lifecycle]] requires a
   human to promote a draft; until that happens nothing is marked to market.
2. **Enough closed trades.** [[Own-record scorecard]] refuses to call anything an edge
   below 20 closed trades, and reports a Wilson interval so a good run is not mistaken
   for skill.
3. **A hypothesis tested properly.** [[C22 Alpha Validation Firewall]] - IC/ICIR,
   multiple-testing correction, walk-forward, costs, capacity - none of it built.
4. **Real-world validation.** Broker fills through [[Kite Connect]], last, once paper
   results justify it.

> [!warning] The honest state
> The brief prints "No closed paper trades yet. The system has made no decision it can be
> scored on." That sentence is the most important line in the product.

Related: [[The closed loop]] · [[C23 Strategy registry and paper trading]] ·
[[Alpha Validation Firewall]]
