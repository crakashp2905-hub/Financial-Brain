---
type: research
tags:
  - research
  - base-rates
  - phase3
date: 2026-09-22
---

# What follows a filing

The first output of [[Historical analogues]]: 37 event types scanned point-in-time as of
2026-09-19, over eleven years of high-materiality disclosures, measured as 90-day excess
against the equal-weighted universe. **14 have enough resolved history to size a trade
on**; the rest return nothing and become a NO TRADE.

| Event type | Cases | Mean excess, 90d |
|---|---:|---:|
| JOINT_VENTURE | 277 | **+1.67%** |
| ORDER_WIN | 5,557 | **+1.49%** |
| FUND_RAISING | 1,132 | +0.62% |
| ACQUISITION | 6,370 | +0.41% |
| PROMOTER_PLEDGE | 7,309 | +0.23% |
| CORPORATE_ACTION | 324 | +0.15% |
| LEGAL_REGULATORY | 8,872 | +0.04% |
| CREDIT_RATING | 8,935 | -0.03% |
| RESULTS | 39,520 | -0.05% |
| MANAGEMENT_CHANGE | 9,995 | -0.21% |
| SCHEME | 2,225 | -1.85% |
| CLARIFICATION | 6,795 | **-2.54%** |
| INSOLVENCY | 144 | **-3.29%** |
| AUDITOR_RESIGNATION | 196 | **-3.58%** |

## What it is evidence of

**The pipeline is measuring something real.** The sign pattern is economically sensible
without having been told to be: auditor resignations and insolvency admissions are the
worst two, clarifications (a company being asked to explain itself) are third worst, and
order wins and joint ventures are the best. Nothing in the construction knows what these
words mean - it counts forward returns. Getting the ordering right by accident is
unlikely, and that is the main thing this table establishes.

**Two entries corroborate things found independently.** `CREDIT_RATING` sits at −0.03%,
and the [[Own-record scorecard]] had already flagged CREDIT_RATING prompts as losers
(4 of 5, −3.2% against +4.7%) from the paper record alone. `CLARIFICATION` at −2.54% is
the same signal [[Event-driven news flow]] was blind to, because "filed something" mixes
an order win with a company being asked to explain a price move.

**`INSIDER_DISCLOSURE` returns zero usable cases**, because the materiality classifier
almost never marks one high. That is the gap [[Following disclosed insiders]] names: the
direction - buy, sell, pledge, release - is in the document and is not being parsed.

## What it is not evidence of

It is **not an edge**, and it has passed no gate. These are unconditional base rates over
the full history: in-sample, uncounted by the trial ledger, and untested by the
[[Alpha Validation Firewall]]. ORDER_WIN's +1.49% mean sits on a **negative median and a
45% win rate** - a right-skewed payoff carried by a minority of large winners, which is
exactly the shape that survives a mean and dies at a cost gate.

The table's job is to let a thesis state a distribution instead of a flat 3% weight. A
positive expected value is a **precondition** for a trade, not a reason to expect one to
work.

Related: [[Historical analogues]] · [[Expected value and sizing]] ·
[[Alpha Validation Firewall]] · [[Twenty-five trades and no edge]] · [[MOC Strategies]]
