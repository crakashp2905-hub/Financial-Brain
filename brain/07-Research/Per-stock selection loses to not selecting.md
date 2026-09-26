---
type: research
tags:
  - research
  - strategy
  - method
date: 2026-09-27
---

# Per-stock selection loses to not selecting

The request was reasonable and specific: each stock should have its own combination of rules
rather than one rule applied to everything. It is also the single most effective way to
fabricate an edge in quantitative finance, so it was built with the dishonest version measured
alongside the honest one.

`evaluation/genome.py`: every 20 sessions, score each of 16 price strategies on each of the 200
most liquid names over a trailing window, hold the winner forward. The return earned takes no
part in the choice.

## The number that matters most is the in-sample one

| selection | net/session | turnover | t |
|---|---:|---:|---:|
| **in-sample** (grades its own homework) | **+0.0381%** | 2.09% | **+6.23** |
| walk-forward, net-of-cost score | −0.0142% | 5.94% | −2.63 |
| walk-forward, gross score | −0.0184% | 7.44% | −3.57 |

**In-sample per-name selection reports about +9% a year at t = +6.23, clearing the Bonferroni
bar of 3.59 comfortably. The walk-forward version of the identical procedure gives −2.63.**

That is a gap of **8.9 t-units of pure selection bias** on the same data, same names, same
strategies, same costs - the only difference being whether the selector saw the returns it
would be graded on. A naive build of this reports a large, highly significant, entirely
fictitious edge, and it would look like the first thing in this project ever to work.

The predicted bias was `sqrt(2 ln 16)` ~ 2.35 t-units, the expected maximum of 16 draws.
Measured is 8.9, because the selector re-optimises at 120 selection points and the bias
compounds across rebalances rather than being one maximum. The arithmetic understated the
danger by nearly fourfold.

## Two bugs in the selector, both found by asking what it chose

**It was scoring gross.** A selector ranking on gross performance prefers whatever trades most:
a high-turnover rule has more trades in the window, so its trailing mean is estimated with less
noise *and* it captures more of any recent move - and the book then pays for every trade out of
sample. Gross selection chose `pivot_breakout` **3,517 times**, the worst strategy in the
library (t = -24.22 at 17.36% turnover). Charging each rule its own entries at that name's own
bucket *inside* the selection window moved the result a full t-unit and dropped that rule from
first pick to fourth. Adverse selection by objective.

**`MIN_TRADES` was 3 entries in a 250-session window.** That disqualified every low-turnover
rule - which is exactly the set that survives costs, because surviving costs *means* trading
rarely. `ma_double_cross_50_200` at 0.78% turnover makes about two entries per 250 sessions per
name, so the selector **could not choose** the one pooled strategy that beats the universe. Set
to 1. Adverse selection by eligibility.

## And after every fix, it still loses to the pooled alternative

| configuration | net/session | turn | t | vs pooled best |
|---|---:|---:|---:|---:|
| gross, 250 sessions, min 3 trades | −0.0184% | 7.44% | −3.57 | −0.0377% |
| net, 250, min 3 | −0.0142% | 5.94% | −2.63 | −0.0335% |
| net, 250, min 1 | −0.0112% | 4.45% | −2.13 | −0.0305% |
| net, 500, min 1 | −0.0101% | 3.61% | −1.60 | −0.0294% |
| net, 750, min 1 | −0.0085% | 3.32% | −1.34 | −0.0278% |
| **`ma_double_cross_50_200` on all 200 names** | **+0.0193%** | **0.78%** | **+2.72** | — |

Every fix helps, monotonically, and none crosses zero. The gap to simply using one rule
everywhere is **stable near 3 bps a session across all five configurations**, which is the
shape of a structural result rather than a tuning problem.

## Why, and what would change it

This is an estimation-noise result, not a claim that stocks are homogeneous. Selection needs a
per-name performance estimate from a finite trailing window, and at 200 names x 16 strategies x
250-750 sessions the noise in that estimate costs more than the heterogeneity gains. The
longer the window the smaller the loss - which is the signature of estimation error, since more
data shrinks it - and the trend points at zero rather than past it.

The principled fix is not a longer window, it is **partial pooling**: shrink each per-name score
toward the pooled score instead of taking the per-name argmax, with the shrinkage intensity
*estimated* from the ratio of within-name to between-name variance (James-Stein, empirical
Bayes) rather than chosen. That has the right property - it reduces to the pooled choice when
the per-name signal is weak, which is what the table above says it is.

Five configurations have now been examined after seeing the first result. That is a search, it
is recorded, and partial pooling needs pre-registration and an out-of-sample window before any
positive from it means anything.

## The ceiling nobody can select past

All 16 strategies in the library were individually rejected, and 15 of the 16 are net negative
on these 200 names. Selection redistributes; it does not create. The two signals that came out
net positive today - `mom_12_1` and `dist_52w_high` - are **cross-sectional ranking** signals
and have no per-name version: "is this name in the top quintile" is a statement about the other
999 names, not about this one.

So per-stock selection is built, correct, and waiting on a library worth selecting from.

Related: [[The horizon was the binding constraint]] ·
[[The tie-break that cost three hypotheses]] · [[Strategy Genome]] ·
[[Running the strategies on noise]]
