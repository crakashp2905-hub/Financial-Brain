---
type: research
tags:
  - research
  - validation
  - mistake
date: 2026-09-22
---

# Beating the median is not an edge

A mistake worth keeping, because it was mine and it was convincing for a day.

The [[Timing model]] was scored on "does this name beat the **median** of its session over
the next 20 sessions". Out of sample it hit 63.8% against a 50% base rate on its most
confident 2.7% of cases, worth **+0.92% net of costs**, and held positive across four
walk-forward folds. It looked like the first real edge in the project.

## The flaw

The Indian cross-section is strongly right-skewed: a minority of large winners pull the
equal-weighted **mean** about **1.2 percentage points** above the median over 20 sessions.
Half the universe beats the median by construction. Nobody can buy the median.

The same 834 accepted cases, rescored:

| Benchmark | Mean excess |
|---|---|
| session median (what it was scored on) | **+0.94%** |
| session mean (what a portfolio earns) | **-0.29%** |

Scored correctly the model cannot even calibrate - no threshold reaches its precision bar -
so it declines to act, which is the designed behaviour.

## Corroboration before the diagnosis

Three portfolio constructions had already rejected it, which is what prompted the
recheck:

- quintile (the firewall's default): **-0.19%** per period after costs;
- concentrated top-25 book: **-0.23%** excess before costs, sign held in 50% of years;
- the book the model itself proposes - hold accepted names, else cash: excess
  **+0.0000** (t = 0.003), **-0.39%** net overall at 65% turnover.

Three constructions disagreeing with the diagnostic is what a measurement error looks
like from the outside.

## What is now permanent

`panel()` benchmarks against the mean, and two tests lock it: one reads the SQL, one
demonstrates on a skewed cross-section why the median flatters. The lesson generalises
past this model: **a benchmark you cannot buy is not a benchmark**, and any metric that
makes a signal look good should be checked against the portfolio that would have to hold
it.

Related: [[Timing model]] · [[Alpha Validation Firewall]] · [[Twenty-five trades and no edge]]
