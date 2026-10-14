# ADR-0004: One evidence standard, and a trial budget

Status: accepted 2026-10-13 (owner instruction: "complete all the steps", adopting the recommended bar)

## Context

Three different bars were in use at once:

* the firewall's Bonferroni bar over `evaluation_runs` (|t| > 3.59 at 151 trials), which only counts trials that
  went through the firewall - none of the 30-odd edge candidates of October did;
* the edge harness's "t >= 2 with the right sign in both 2016-21 and 2022-26" (`evaluation/edges.judge`);
* `h20`'s out-of-sample confirmation against a synthetic null.

A candidate could pass one and fail another, and nothing said which counted. The Bonferroni bar also rises with
every weak idea tried, so each new trial makes the strong ones harder to confirm - the ledger was spending its own
power.

## Decision

A strategy has an edge only when it passes **every** gate below, in order. `evaluation/standard.py` implements them.

| Gate | Requirement |
|---|---|
| G1 pre-registered | a `PRE-REGISTERED <name>.md` note committed before any result (audited by `research/preregistry.py`) |
| G2 net of costs | per-name round trips by liquidity bucket, point in time; the series judged is net |
| G3 replication | same sign, \|t\| >= 2, in **both** 2016-2021 and 2022-2026 |
| G4 null separation | the replication statistic min(t_a, t_b) beats the 95th percentile of the same statistic over random books with the **same number of names and the same turnover** drawn from the same universe |
| G5 out of sample | positive net mean over >= 12 rebalances after the registration's data cutoff, and the live paper record inside the backtest's 90% band (`evaluation/oos_monitor.py`) |

Verdicts: G1-G4 pass -> `CANDIDATE` (paper-traded, sized at zero real capital); G5 pass -> `CONFIRMED`. Nothing
below `CONFIRMED` may be considered for real money (ADR-0005).

The Bonferroni bar is still reported beside every result, as information about the size of the search; it is no
longer the gate. G3 is the stronger test: two independent halves at |t| >= 2 have joint probability
2·Φ(-2)² ≈ 1.0e-3 under the null, and a fitted number has no reason to reappear in the other half.

## The trial budget

At most **8 new trials per calendar quarter**. A trial is a pre-registered test that produces a verdict; every one
is appended to `docs/trials/ledger.jsonl` (append-only, committed, so git dates every entry).
`standard.check_budget()` refuses a ninth. Each trial must name a published mechanism or an orthogonal data source.

## What stops

* new technical indicators on price alone (322 tested; none counts as named; ADX flips sign between tiers);
* more learners on the same features (forest, SVM, MLP, LSTM, genetic: none beat LightGBM or momentum);
* intraday direction strategies (no gross edge measured);
* changing any rule because of paper P&L - a losing context becomes a hypothesis, never a fix.

## Consequences

* The bar no longer drifts upward with unrelated trials, but the budget caps how fast ideas are spent.
* G4 makes the harness test itself: a strategy that only beats an equal-turnover random book by luck fails.
* G5 takes calendar time that no analysis shortens. At a 20-session rebalance, 12 rebalances is about 12 months.

## Amendment 1 (2026-10-14, owner decision)

2026Q4's budget is raised from 8 to **9**, for one trial only: the family test registered in
`PRE-REGISTERED index mean reversion and VWAP` (nine rules, multiplicity stated inside it). The owner chose this over
waiting for 2027Q1, when asked. The cap is otherwise unchanged; `standard.BUDGET_OVERRIDES` records the exception so
the ledger enforces it.

## Amendment 2 (2026-10-14, owner decision)

2026Q4's budget is raised from 9 to **10**, for one trial only: the family test registered in
`PRE-REGISTERED published price anomalies` (the catalogue's untested price anomalies, multiplicity stated inside it,
judged additionally by White's Reality Check / Hansen's SPA and the probability of backtest overfitting across the
family). Asked whether to run now or wait for 2027Q1, the owner answered: "do it in detail but do all of it with
depth".
