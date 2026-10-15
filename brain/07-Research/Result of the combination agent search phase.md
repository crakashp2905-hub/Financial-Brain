---
type: research
tags:
  - research
  - result
  - agent
date: 2026-10-15
---

# Result of the combination agent search phase

Search phase of [[PRE-REGISTERED combination agent search]], on 2016-2022 only. Validation (2023-2026) is locked until a
trial is available. Code `research/combo_agent.py`; every combination tried is in `data/knowledge.duckdb`
(`agent_combos`, runs `agent_20261015_171015` and `agent_fullgrid_*`).

## The registered run: System One prior, 229 combinations, nothing selected

The local model (qwen2.5:7b, typed answers, definitions only) judged 281 of 510 (trigger, context) pairings to have no
mechanism and chose a side and horizon for 229. **It chose a one-session hold for 210 of them** - the chart trader's
default. At one session a 20 bp round trip dominates: the eligible combinations' median t was **-2.66**, the best
+1.46, and none reached the selection bar (t >= 3, 60 events, 24 months).

The prior did reduce the family 13-fold. **It pruned in the wrong direction**: it kept the horizon where costs bite hardest
and mostly discarded the 20-session holds that fare least badly. A System One model asked what a signal "implies"
returns trading folklore, not cost-aware judgement - its value here is the typed, logged, countable choice, not its taste.

## Diagnostic beyond the registration: the full grid, 3,060 combinations

Run to measure what the prior cost; its 3,060 tries are added to the family (3,289 in all).

| side, horizon | combinations | median t | best t |
|---|---|---|---|
| long, 1 session | 483 | -2.81 | 1.35 |
| long, 5 | 483 | -1.41 | 2.21 |
| long, 20 | 483 | -0.76 | 2.09 |
| short, 1 | 483 | -2.87 | 1.46 |
| short, 5 | 483 | -1.48 | 2.42 |
| short, 20 | 483 | -0.63 | 3.11 |

Two selected, one idea: **short the one-year Fibonacci "golden pocket" bullish signal for 20 sessions** (with the Nifty
above its 200-session mean, t 3.11; or the stock below its own, t 3.11; unconditioned, 2.78). The best of 3,060 tests
on pure noise is expected near **t 4.0**, so 3.11 is *below* what luck alone produces at this search size. It goes to the
locked validation like any selection, with that stated.

## What the search says

* Across every chart, candle and indicator trigger, every context, both sides: the outcome is decided by the holding
  period's cost, not by the signal. Median t rises from -2.8 at one session to -0.6 at twenty, on both sides alike.
* No combination of triggers and contexts produces a search-sample edge that clears its own search penalty.
* The knowledge base now holds all 3,289 tried combinations, so no future search re-tries them blind.

Information, not advice.
