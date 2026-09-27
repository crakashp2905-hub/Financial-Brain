---
type: research
tags:
  - research
  - firewall
  - method
date: 2026-09-27
---

# A hundred and fifty trials are not a hundred and fifty discoveries

The Bonferroni bar is computed over every trial in the ledger, which assumes each one is an
**independent** hypothesis. The ledger says otherwise:

    mom_12_1        16 trials          momentum      69 trials
    vol_60          16                 reversal      25
    dist_52w_high   15                 volatility    25
    above_ma200     15                 events        16
    ret_20d         14                 candles        7
                                       volume         5
    76 of 150 on five features         unassigned     3

Seventy-six of the 150 sit on five features, and those five are about three economic ideas. "We
tested momentum sixteen ways" is one search with a budget of sixteen. Correcting for it as though it
were sixteen discoveries answers a question nobody asked.

## This is the argument a motivated reasoner reaches for

`dist_52w_high` sits at an alpha t of **+3.12 against a bar of 3.59**. A family-based bar is lower.
`evaluation/families.py` was written immediately after the result that needed a lower one, and that
is worth stating rather than hoping nobody notices.

So it has two guards, and they are the whole design.

**The correction cuts both ways.** Across families the bar falls because there are fewer independent
ideas. *Within* a family the representative statistic is **penalised for its own search**: the best
of *m* correlated variants is inflated by up to `sqrt(2 ln m)`. Both halves or neither - taking the
lower bar without the penalty is the manipulation, not the correction.

**And on this ledger the penalty is larger than the relief:**

    bar by trial                       3.59
    bar by family                      2.73        the bar falls by 0.86
    momentum family has spent          69 trials
    search penalty (upper bound)      -2.91        the statistic falls by 2.91
    ------------------------------------------
    dist_52w_high  +3.12  ->  +0.21   against 2.73  ->  REJECT

Family framing moves the candidate **further from passing, not closer**. That is the result that
makes the module trustworthy, and it is the opposite of what the reasoning was reaching for.

**A taxonomy declared after a result cannot promote it.** `DECLARED_AFTER = 2026-09-27` is enforced:
the families were chosen with today's statistics already visible, so the family bar may *describe*
the existing 150 trials and may not promote any of them. It applies from here on.

## What cannot be measured yet, and the schema change that fixes it

Done properly with Bonferroni at both levels, the two corrections cancel exactly back to the trial
count. **The entire gain comes from correlation between tests inside a family** - and correlation has
to be measured.

The right statistic is the effective number of independent tests, from the eigenvalue spectrum of the
trials' IC correlation matrix. It is the same statistic `risk/decompose.effective_bets` computes for a
portfolio, for the same reason: counting positions overstates diversification exactly as counting
trials overstates evidence. Validated on constructed series - six trials that are really two ideas
come back at **1.95**.

It could not be computed, because `evaluation_runs` stored `mean_ic` and `ic_t` and **not the
per-rebalance IC series**. So the effective count could only be bounded to **[7, 150]**, and an
interval that wide decides nothing.

`ic_series` and `ic_dates` are now columns, the firewall persists what it already computed, and
`effective_tests()` returns `None` with a reason rather than interpolating. The existing 150 predate
the column and cannot be retrofitted without re-running them; the interval closes as new trials
accumulate.

## Why this matters beyond one candidate

The ledger is the mechanism that makes every other result in this project honest, and it had a
counting error at its centre. Not a wrong number - a wrong **unit**. Bonferroni over 150 trials is
either too harsh (they are not independent) or too generous (a 69-trial family search is not one
test), and until the effective count is measurable the bar is provably one of the two without saying
which.

That is a different kind of defect from the six found earlier today. Those were statistics answering
a different question than their name. This is the right statistic computed over the wrong population.

Related: [[Why nothing passes]] · [[Alpha Validation Firewall]] · [[h20]] ·
[[Running the strategies on noise]] · [[Per-stock selection loses to not selecting]]
