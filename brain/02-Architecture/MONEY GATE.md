---
type: concept
tags:
  - concept
  - validation
  - firewall
---

# MONEY GATE

Nineteen checks, one verdict, and the only question it answers is **may this strategy be given
money?**

Almost every check already existed. That was the problem. They were spread across
`evaluation/firewall.py`, `evaluation/implementability.py`, `evaluation/registry.py`,
`decisions/safety.py`, `risk/limits.py` and `portfolio/gate.py`, each producing its own verdict, and
nothing anywhere combined them. A reader wanting the answer had to know which six modules to consult
and how to weigh them - which means in practice nobody did, and a strategy could look approved
because the gate someone happened to run said PASS.

`evaluation/money_gate.py` computes nothing of its own. Every check delegates to the module that
owns it, and the value is entirely in there being **one place that says no**.

## The two things it refuses to be talked into

**A high Sharpe is not a reason.** Sharpe appears once, inside `deflated_sharpe`, corrected for how
many trials produced it. A strategy arrives with a good backtest by construction - that is why
anyone is looking at it.

**Nothing is a warning.** A check passes or the gate fails. There is no "passed with reservations",
because a reservation is how a failing strategy reaches capital: somebody reads it, decides it is
acceptable, and the gate has been converted into advice. If a condition is tolerable it should not
be a check.

## Absent is not passed

Three states, not two. `UNAVAILABLE` fails the gate exactly as `FAIL` does, and is kept distinct
because the two call for entirely different work - "we have not measured this" against "we measured
it and it failed".

Four are currently unavailable on this archive:

| check | why |
|---|---|
| `spread` | Corwin-Schultz (2012) gave spreads tenfold too large here; declared unusable |
| `sector_exposure` | no industry classification; `index_constituents` has zero rows |
| `paper_performance` | needs 120 sessions of live paper record; none exists |
| `execution_simulation` | until run, per name — see [[Execution was not where the edge died]] |

Letting any of those read as green is how an unmeasured risk becomes an approved one.

## Significance is asked twice, and that is the design

`ic_significance` asks whether the signal **orders** names. `net_significance` asks whether the
ordering **pays**. They are different questions and in this archive they routinely disagree:
`vol_60` holds the largest IC on record at **+12.30** with *negative* alpha, and `dist_52w_high`
clears on IC t **+3.77** with a net t of **+1.59**.

A money gate asking only the first would approve on the strength of an ordering nobody can harvest -
which is precisely the conflation that had the opportunity engine reporting CLEARS for a signal with
negative alpha, and it would be worse here.

## What it says about the leading candidate

`dist_52w_high` at h=20, the closest thing this project has to a candidate:

    NO_CAPITAL   10 of 19 passed   4 failed   5 unavailable

    PASS   pit_correctness, survivorship, look_ahead
    PASS   ic_significance         IC t +3.77 against a bar of 3.59
    FAIL   net_significance        net t +1.59 against a bar of 3.59
    FAIL   deflated_sharpe         DSR 0.00
    PASS   walk_forward            sign held across 11 years
    PASS   regime_robustness       5 regimes, version-pinned
    PASS   null_separation         real minus null = +5.04 t
    FAIL   out_of_sample           0 rebalances after the cutoff
    PASS   transaction_costs       0.72% round trip on its own liquidity mix
    PASS   capacity, benchmark_comparison
    FAIL   portfolio_fit           the portfolio gate says ABSTAIN
    UNAVAILABLE  spread, execution_simulation, liquidity, sector_exposure,
                 paper_performance

Ten passes is not most of the way there. The four failures are each sufficient on their own, and the
five unavailable checks are work nobody has done rather than risks nobody has.

Related: [[Alpha Validation Firewall]] · [[Execution was not where the edge died]] ·
[[Why nothing passes]] · [[h20]] · [[The toll was measured with one number and it needed thousands]]
