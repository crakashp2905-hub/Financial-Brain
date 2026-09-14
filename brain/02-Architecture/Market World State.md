---
type: concept
tags:
  - concept
  - contract
---

# Market World State

Agents reason from a **versioned snapshot**, not by independently browsing and improvising.

Contains: price, liquidity, volatility, breadth, index and sector state; fundamentals,
filings, events, disclosure status; macro/regime variables with data-as-of time; graph
relationships; portfolio positions, cash, exposures, constraints, open orders;
unresolved hypotheses, invalidations, anomalies, missing data; the evidence set and its
quality assessment.

The orchestrator may commission research producing *candidate* evidence. Only verified,
typed evidence updates the state. **Every decision references exactly one immutable
version.** Built as [[C08 World state]].
