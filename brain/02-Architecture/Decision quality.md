---
type: concept
tags:
  - concept
  - decision-engine
  - phase3
updated: 2026-09-22
---

# Decision quality

A good decision can lose money and a bad one can make it. A system that learns only from
P&L therefore learns the wrong lesson roughly as often as the right one: it punishes a
well-reasoned trade that met a bad tape, and rewards a reckless one that got lucky.

So process is scored on its own terms, from the record, **before any outcome is known**:

| Dimension | What earns the mark |
|---|---|
| evidence | both sides cited, and the citations still current in the [[Evidence ledger]] |
| thesis | a named mechanism and a named primary uncertainty |
| falsifiability | invalidation conditions something can actually re-check |
| arithmetic | scenarios that sum to one, with a loss case, and a positive EV |
| sizing | the position follows from the distance to invalidation |
| process | the safety gate was consulted and its refusals respected |

## What it found

Run over the 25 closed paper trades, `fb quality` returned a result that was more useful
than the headline:

```
mean process quality 68%, all 25 "well made"

  arithmetic        0%     x25  needs at least 2 scenarios; one scenario is a forecast
  sizing           30%     x25  a weight with no invalidation distance behind it
  process          80%
  evidence        100%
  thesis          100%
  falsifiability  100%
```

The committee argues both sides, cites the ledger, and names what would prove it wrong -
and then buys a flat 3% of the book with **no distribution and no reason for the figure**.
Two dimensions at or near zero, four at or near full marks. That is a measurement, not an
opinion, and it named the next thing to build: [[Historical analogues]] for the
distribution, and the drawdown invalidation already in the thesis for the size.

## The comparison that matters

`against_outcomes()` splits closed trades into well-made and poorly-made and reports the
**quality premium** between them. Consistently high quality with poor returns says the
**edge is absent rather than the process broken** - which is exactly the state
[[Twenty-five trades and no edge]] describes. The reverse would say the system got away
with something it should not repeat.

Related: [[Expected value and sizing]] · [[Historical analogues]] ·
[[Twenty-five trades and no edge]] · [[Learning from losses]]
