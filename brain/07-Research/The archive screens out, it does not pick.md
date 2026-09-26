---
type: research
tags:
  - research
  - events
  - firewall
date: 2026-09-26
---

# The archive screens out, it does not pick

Twelve event types tested separately for the first time, each held 90 sessions after a
high-materiality filing. All twelve rejected - and the pattern in *how* they rejected is
the first genuinely actionable thing this project has found.

## The results

| Event | t | net/session | held | what failed |
|---|---:|---:|---:|---|
| **CLARIFICATION** | **-6.73** | -0.0367% | **191** | costs, DSR only |
| PROMOTER_PLEDGE | -2.89 | -0.0173% | 99 | sig, costs, DSR, wf, regime |
| AUDITOR_RESIGNATION | -2.77 | -0.0866% | 6 | sig, costs, DSR, capacity |
| MANAGEMENT_CHANGE | -2.35 | -0.0255% | 206 | sig, costs, DSR |
| SCHEME | -2.08 | -0.0163% | 59 | sig, costs, DSR |
| **JOINT_VENTURE** | +2.24 | **+0.0417%** | 10 | sig, DSR, **capacity** |
| **ACQUISITION** | +2.09 | +0.0141% | 129 | sig, DSR, wf |
| ORDER_WIN | +0.91 | +0.0079% | 81 | sig, DSR, wf |

## The finding

**The event archive is a screen, not a stock picker.**

Four types reach |t| > 2 as *longs that lose*, and two of them hold enough names to be
usable. The strongest is CLARIFICATION at **t = -6.73 across 191 names** - the largest
statistic of either sign this project has produced. Being asked to explain yourself to an
exchange is followed by underperformance, consistently, across 6,795 events and eleven
years.

It fails the cost gate, and that is the point rather than a disappointment: **you cannot
profit by buying it, but you can profit by not buying it.** Avoidance costs nothing.
A trade costs 71 basis points. Every negative result above is free to act on and none of
the positive ones are.

That asymmetry has been implicit in this project since the constitution was written. This
is the first time it has been measured.

## The positives died exactly where the prior said

JOINT_VENTURE has the best net excess of any event, **+0.0417%**, and *passes the cost
gate*. It holds ten names. [[h16]] predicted this in advance - "277 filings across eleven
years is a handful of names at any moment" - and capacity is what killed it.

ACQUISITION is the closest thing to a tradeable positive: passes cost **and** capacity,
129 names, +0.0141% at t = +2.09 against a bar of 3.4.

## Two disagreements with the literature, resolved

The analogue base rates had disagreed with their own papers in sign. Proper testing
settled both, and the papers won one each:

* **FUND_RAISING** measured +0.62% unconditionally; tested properly it is -0.0098% at
  t = -0.73. Loughran & Ritter (1995) documented issuers underperforming, and the positive
  base rate did not survive costs, regimes or walk-forward. The paper is supported.
* **SCHEME** stayed negative, t = -2.08. Cusatis, Miles & Woolridge (1993) find spun-off
  units outperform; that does not hold here.

A base rate is not a result. Both of these looked like findings for a week.

## Why the aggregate features failed

`news_20d` counted any high-materiality filing and was rejected. This is why: it averaged
CLARIFICATION at -0.0367% against JOINT_VENTURE at +0.0417% and got nothing. The same
defect as the insider feature, which mixed compliance certificates with dealings.

**Counting events of different meanings is not a signal.** Separating them turns a null
result into four significant ones, all negative, all free to use.

## What is void rather than rejected

BUYBACK held zero names: every one of its 5,803 filings is classified `medium`
materiality, so the high-materiality filter excludes them all. That is a classifier
question, not a market result, and the entry stays open.

Related: [[h16]] · [[What follows a filing]] ·
[[The insider feature contained no insider trades]] · [[C14 Investment Constitution]] ·
[[Alpha Validation Firewall]]
