---
type: research
tags:
  - research
  - strategy
  - data
date: 2026-09-26
---

# A hundred strategies, and the data that blocks half of them

103 published anomalies catalogued from 99 distinct papers, each carrying its claim, its
source, the inputs it needs, and whether this project holds them. Building it produced a
more useful finding than any single strategy has.

## The split

    ready to test now        60
    blocked on fundamentals  35
    blocked on external data  8

`financial_results` holds **20 rows**. `filing_facts` holds **1**. There is no book value,
no earnings, no assets, no share count, no cash flow anywhere in this system.

## Why that is the finding

The 35 blocked entries are not a random third of the literature. They are **value,
profitability, investment and accruals** - the Fama-French five-factor model, the
Hou-Xue-Zhang q-factor model, Piotroski, Sloan, Novy-Marx. The best-replicated results in
asset pricing, every one of them fundamental.

Meanwhile this project has now tested roughly **twenty price and technical factors and
rejected all of them**. That is not bad luck. Hou, Xue & Zhang (2020) replicated 452
anomalies and found 65% failed; the ones that failed hardest were the price-only ones.
McLean & Pontiff (2016) found published anomalies decay 58% after publication, and
price-based signals decay fastest because they are cheapest to copy.

**So the binding constraint has moved.** It was "no edge"; then it was "the cost
assumption"; then it was "turnover". It is now **data**. Testing another twenty technical
signals on the same price series is not a research programme, it is a way to raise our own
Bonferroni bar.

Ranked by how many strategies each missing input unblocks:

    financials    6      debt           3
    assets        5      book equity    2
    earnings      4      cash flow      2

## Where this archive is unusually strong

Not everything is blocked. The event families are **better than a typical retail setup**:
3.08M classified announcements, with base rates already measured for fourteen event types.
Several catalogue entries have measured support waiting for a proper test:

| Entry | Paper | Measured here |
|---|---|---|
| auditor_resignation | Wells & Loudder (1997) | **-3.58%** over 196 cases |
| insolvency_filing | Campbell, Hilscher & Szilagyi (2008) | -3.29% |
| results_correction | Hribar & Jenkins (2004) | -2.54% over 6,795 cases |
| joint_venture | McConnell & Nantell (1985) | **+1.67%** |
| promoter_pledge | India-specific | 27,688 filings, no US equivalent |

Two of them disagree with their papers in *sign* - FUND_RAISING measured +0.62% where
Loughran & Ritter (1995) predict underperformance, and SCHEME measured -1.85% where
Cusatis et al. (1993) predict the opposite. Those are the interesting ones.

## On the trial cost of breadth

Adding a hundred strategies moves the Bonferroni bar from |t| > 3.35 to |t| > 3.57. That
is a real but modest cost, and worth stating against the literature: Harvey, Liu & Zhu
(2016) surveyed 316 published factors and argued the hurdle should be **t > 3.0**. This
project is already stricter than the paper that made strictness famous, and would remain so
at 450 trials.

Breadth is cheap. What is expensive is breadth reported selectively, and the shared ledger
is what prevents that.

Related: [[MOC Strategies]] · [[Alpha Validation Firewall]] · [[Why nothing passes]] ·
[[Intraday breakouts pay seven times their edge]]
