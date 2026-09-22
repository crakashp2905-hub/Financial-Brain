---
type: concept
tags:
  - concept
  - decision-engine
  - phase3
updated: 2026-09-22
---

# Historical analogues

Where a scenario set comes from, and why it may not come from a model.

A decision needs a distribution: what is made when right, what is lost when wrong, and how
likely each is. Two ways to get one:

| Source | Can it be wrong? | Provenance |
|---|---|---|
| ask a language model for "probability of the bull case" | no - nothing checks it | none |
| count what followed this event type before | yes, and visibly | every case, by date |

Only the second is usable, so scenarios are built by counting.

## Construction

    this event type, as of this date
        -> every prior instance whose horizon had already closed
        -> forward excess return against the equal-weighted universe
        -> bear / base / bull as terciles, weighted by how many cases fell in each

Three rules do the work:

* **Point in time.** Only instances resolved on or before `as_of`. Scenarios for a 2021
  decision cannot contain 2024's outcomes - without this every downstream backtest is
  meaningless.
* **A benchmark you could buy.** Excess is against the universe *mean* over the same
  entry and exit sessions, never the median. [[Beating the median is not an edge]] is the
  day that lesson cost a convincing result.
* **Enough cases or nothing.** Under 30 resolved cases it returns no scenarios, the
  decision fails its arithmetic at risk review, and the answer is NO TRADE. An invented
  distribution is worse than an absent one, because it passes.

## Why it was built

Scoring the committee's own record with [[Decision quality]] gave a number that was hard
to argue with: over 25 closed trades it scored **100% on evidence, thesis and
falsifiability - and 0% on arithmetic, 30% on sizing.** It argued both sides, cited the
ledger, named what would prove it wrong, and then bought a flat 3% of the book with
nothing behind the figure.

Analogues close the first gap; the drawdown invalidation that was already in the thesis
closes the second, through [[Expected value and sizing]].

## What it is not

It is not an edge. `ORDER_WIN` over 5,158 resolved cases returns mean excess **+1.6%**
with a **-2.0% median** and a **45% win rate** - a right-skewed, lottery-shaped payoff
where the average is carried by a minority of large winners. A positive expected value is
a *precondition* for a trade, not a reason to expect one to work. The [[Alpha Validation Firewall]] still has to pass anything built on it,
and so far it has passed nothing - see [[MOC Strategies]].

Related: [[Expected value and sizing]] · [[Decision quality]] · [[Investment Committee]] ·
[[Alpha Validation Firewall]]
