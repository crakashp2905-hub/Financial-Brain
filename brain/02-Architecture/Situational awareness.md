---
type: concept
tags:
  - concept
  - validation
---

# Situational awareness

A decision is made *somewhere*: in a regime, after a particular kind of filing, in a name
of a particular liquidity. The system records all of it at entry, so a later question -
"do our calls only work in RISK_ON?" - is a query rather than a recollection.

Where it is already wired:

- [[C07 Market Regime Brain]] classifies every session, and the regime **on the entry
  date** goes into the trade's postmortem, not today's regime.
- [[Base rates]] answer "is this unusual for this company, and against the market".
- [[C17 Decision record and lifecycle]] keeps the world-state version the decision was
  made from, so the whole context is replayable.
- [[Learning from losses]] turns those features into rules once enough cases exist.

What it is not: a feeling about the market. Every element is a stored value with a date
attached, and a claim built on it cites that value.

Related: [[C08 World state]] · [[Four timestamps]]
