---
type: research
tags:
  - research
  - validation
date: 2026-09-21
---

# First measured record

The first time this system has been scored on anything.

`fb replay` rebuilt the world state as of six past sessions (Jan-Jun 2026), ran the
[[C16 Investment Committee]] on companies that filed something material that day, walked
each draft to paper and closed it at its 90-day horizon against prices already held.

| | |
|---|---|
| Drafts | 9 (one debate produced no cited points, so no draft) |
| Reached paper | 6 |
| Closed | 6 |
| **Hit rate** | **33%** (2 of 6) |
| **Mean excess** | **-1.01%** per trade, net of costs, against the Nifty |
| Spread | worst -18.9%, best +17.4%, median -4.1% |

The trades:

| Entry | Exit | Stock | Nifty | Excess |
|---|---|---|---|---|
| 2026-03-17 | 2026-06-15 | -6.5% | +1.2% | -8.1% |
| 2026-04-16 | 2026-07-15 | +17.7% | -0.5% | **+17.4%** |
| 2026-04-16 | 2026-07-15 | +15.0% | -0.5% | **+14.8%** |
| 2026-05-18 | 2026-08-17 | +2.9% | +2.7% | -0.2% |
| 2026-06-16 | 2026-09-15 | -14.4% | -3.6% | -11.2% |
| 2026-06-16 | 2026-09-15 | -21.8% | -3.6% | **-18.9%** |

## What this does and does not say

[[Own-record scorecard]] refuses to call it anything: six of the twenty closed trades it
requires, and the Wilson interval on 2-of-6 spans almost the whole range. **It is not
evidence that the committee is bad, and certainly not that it is good.** Two trades carry
most of the result in each direction, which is what a six-trade sample looks like.

What it *is*: proof the loop runs end to end - world state -> dossier -> debate -> chair
-> constitution -> paper -> mark -> score - and a baseline that any later change has to
beat. Before this, the honest answer to "does it pick well?" was not "no", it was
**"nothing has ever been measured"**.

## What would make it mean something
- More sessions (each costs ~12 minutes of local inference, so this is patience, not
  engineering)
- A **control**: the same universe bought blindly, to separate the committee from the
  "companies that filed something material" effect
- [[C22 Alpha Validation Firewall]] before any of it is called a strategy

Related: [[The unanswered question]] · [[C23 Strategy registry and paper trading]]
