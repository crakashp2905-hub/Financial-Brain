# ADR-0006: A small-allocation tier for modest edges (PROPOSED - not in force)

Status: **proposed 2026-10-15, awaiting the owner's decision.** Nothing in the code reads this ADR; `rollout.eligible`
is unchanged. Written because the owner asked for "all your recommendations", and the recommendation was to write
this up for a decision, not to apply it.

## The problem

ADR-0004's G3 asks for |t| >= 2 in **each** six-year half. Expected t over six years is about Sharpe x sqrt(6), so G3
needs a Sharpe above ~0.8 a year. Durable equity edges in the literature run 0.3-0.7. Measured here:

| strategy | full-sample t | half t | annual Sharpe (approx.) | years to t = 2 at that Sharpe |
|---|---|---|---|---|
| M1 cost-aware momentum book | 1.86 | 1.45 / 1.24 | 0.58 | ~12 |
| C1 rich-basis carry (>10% basis) | 2.40 | 1.44 / 3.89 | ~0.9 (months with gaps; overstated) | ~5 |

Both are positive in both halves and far above their random nulls (G4). Under ADR-0004 neither can reach stage TICKET
until G5 confirms them out of sample - about 12 months for C1 and 23 for M1. The question is whether a modest edge may
earn a **small, human-approved** allocation sooner, so that real fills and costs are measured while the out-of-sample
record accumulates.

## The proposal

A new verdict, `CANDIDATE_SMALL`, below `CANDIDATE`:

1. positive net mean in **both** halves (2016-21, 2022-26);
2. G4 passed (above the 95th percentile of its random null);
3. full-sample t >= **2.5** on the registered series (one test over the whole period, not one per half);
4. family checks not contradicting it (SPA/PBO where it was part of a family);
5. a live paper record of at least 40 sessions, inside the backtest's 90% band.

`CANDIDATE_SMALL` may reach **stage TICKET only** - every order approved by the owner - with total deployed capital
at most **Rs 25,000**, and never `SMALL_AUTO` or above. Demotion rules are unchanged (any reconciliation mismatch,
kill-switch trip or band breach drops it).

## Who qualifies today: nobody

At the 2.5 threshold proposed above, **neither strategy qualifies**: C1 is t 2.40 on its registered series, M1 is
1.86. (An earlier message in the conversation said carry "would qualify"; that was wrong - the 2.40 was computed in the
same answer.) The threshold was proposed before these numbers were looked at for this purpose, and **it should not be
moved to admit carry**: choosing a bar after seeing which side of it a strategy sits on is the fitting this project
exists to prevent. C1 may reach it as live months accumulate (its 2022-26 half is t 3.89).

## Risks, stated

* A lower bar admits more false edges. At t >= 2.5 over the full sample, plus both halves positive and G4, the chance
  a pure-noise strategy qualifies is small (well under 1%) - but the families already searched here number in the
  hundreds, so some noise will get close.
* Real money on a modest edge loses money in most short windows: a Sharpe-0.6 strategy has roughly a 30% chance of
  being down over any single year. The cap (Rs 25,000, owner-approved orders) bounds that cost; it does not remove it.
* The point of the tier is **measurement** (real fills, real costs, real slippage against the simulator), not profit.

## Decision needed from the owner

Accept, reject, or change the threshold/cap - **before** looking at whether any strategy then qualifies.
