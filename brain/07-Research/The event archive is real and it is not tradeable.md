---
type: research
tags:
  - research
  - events
  - validation
  - walk-forward
date: 2026-09-29
---

# The event archive is real and it is not tradeable

[[Naive momentum beats it, and the horizon was a fit]] left one question: `ORDER BY mom_12_1 DESC`
beat every signal built on eleven years of point-in-time data, so what does the rest of it do? The
honest place to look is the asset that is genuinely unusual - 3.08M announcements reduced to 326,179
typed event rows keyed on lineage, 2015 to 2026, eleven usable event types with 605 to 25,791 cases
each. It had never been tested as a signal.

**It contains real, momentum-orthogonal, statistically significant information. It does not survive
being selected out of sample.** Both halves matter.

## The finding: four effects clear the Bonferroni bar

Every event is compared against a control **from the same session and the same momentum decile**. An
unmatched study rediscovers that events happen to stocks already moving - order wins are announced by
companies doing well, insolvencies by companies already down - so the matching is the experiment. The
statistic is computed on the per-session effect series, not per event, because events cluster in time
and treating 11,931 order wins as independent inflates the t by roughly the events per date.

Matched on twelve-month momentum **and** the last month's return, h20 and h60, against a Bonferroni bar
of **3.625** over the 151 trials already spent plus the 22 this census adds:

    event                     h    effect       t   sessions    cases
    e_scheme                 20    -2.06%   -6.38      1,496    2,764   CLEARS
    e_clarification          60    -2.04%   -5.73      2,338    8,397   CLEARS
    e_scheme                 60    -3.01%   -5.32      1,456    2,626   CLEARS
    e_management_change      20    -0.74%   -3.90      1,565   10,394   CLEARS
    e_fund_raising           20    -1.35%   -3.49        845    1,185
    e_clarification          20    -0.62%   -2.54      2,380    8,630
    e_auditor_resignation    20    -2.32%   -1.96        138      178
    e_credit_rating          20    -0.33%   -1.89      2,395   10,167

    significant at nominal 5%: 7, where chance alone predicts 1.1

**Every effect that clears the bar is negative**, which is economically coherent: these are distress
and uncertainty events. A scheme of arrangement - merger, demerger, restructuring - costs 2.06% over 20
sessions and 3.01% over 60 against momentum-matched peers. An exchange-demanded clarification costs
2.04% over 60.

**They survive the control that should have killed them.** An exchange demands a clarification
*because* a price moved, so the flag is partly a marker of a move that already happened. Adding
``ret_20d`` to the match differences that out - and three of the four got *stronger*:

    event                     h    t (momentum only)   t (+ recent return)
    e_scheme                 20          -6.12                -6.38
    e_scheme                 60          -5.11                -5.32
    e_clarification          60          -4.35                -5.73
    e_management_change      20          -4.00                -3.90

This is the answer to the question the baselines posed. **Momentum cannot see a scheme-of-arrangement
filing.** The archive holds information a price sort does not.

## And in-sample it looks tradeable

The effects are negative and the book is long-only, so the only tradeable form is an exclusion filter:
decline to hold names carrying one of the cleared flags within the last 60 sessions. Eleven years, h60,
top 100:

    strategy                                excess    Sharpe    maxDD
    mom_12_1                              +585.40%     +1.13    -51.8%
    mom_12_1, minus flagged names         +668.31%     +1.18    -50.4%
    dist_52w_high                         +246.55%     +1.18    -39.3%
    dist_52w_high, minus flagged names    +358.55%     +1.28    -40.7%

**+82.9 points on momentum, +112.0 on the candidate**, with better Sharpe on both and a shallower
drawdown on momentum. Additive to two different signals, which is what orthogonality predicts.

Every number above is in-sample.

## Walked forward, it fails

The exclusion set is chosen by running the census on the training window **only**, taking whatever
clears the bar there, and applying exactly that set to the next window. Same folds and purge as the
horizon test:

    fold  chose on train                                   plain   filtered    delta
     0    clarification, promoter_pledge, scheme         +32.94%    +29.25%   -3.69%
     1    clarification, order_win, promoter_pledge,
          scheme                                        +12.72%     -0.62%  -13.34%
     2    clarification, management_change, scheme        +0.55%     +3.98%   +3.43%

    mean out-of-sample excess   plain +15.40%   filtered +10.87%   delta -4.53%
    mean out-of-sample Sharpe   plain  +1.46    filtered  +1.41
    folds where the filter helped: 1 of 3

An in-sample gain of +82.9 points becomes an out-of-sample loss of 4.53. The same shape as h60.

**The selected set is unstable, and that is the diagnosis.** Only `clarification` and `scheme` are
chosen in all three folds. `promoter_pledge` clears on two, `order_win` on one, `management_change` on
one. A filter whose membership changes every time it is refitted is fitting the window, and the
effects that are robust are not the ones that carry the gain.

## What did survive: the baseline itself

The most useful number in this note is in the column nobody was testing.

    mom_12_1 at h60, out of sample:  +32.94%, +12.72%, +0.55%
    mean +15.40%, Sharpe +1.46, positive in 3 of 3 folds

Against `dist_52w_high`'s horizon selection over the same folds: **-17.45%, -4.75%, -0.26%**, negative
in 3 of 3.

So the picture after two walk-forward tests is consistent: **naive twelve-month momentum has positive
out-of-sample excess over its own universe, and every attempt to improve it - a swept rebalance
horizon, an event exclusion filter - makes it worse out of sample.** That is not a comfortable result
but it is a clear one, and it is the first out-of-sample positive this project has.

## What is left to try, and what is not

**Not another variant of this filter.** Trying the stable two-flag set because it appeared in all three
folds uses out-of-sample information to make the choice, which is how the next in-sample gain gets
manufactured. If it is worth testing, it has to be pre-registered and tested on data none of this
touched.

**The events may be tradeable in a form this book cannot take.** Every significant effect is negative,
and a long-only top-100 book can only decline to hold - which turns out to cost more in forgone
momentum than it saves in avoided losses. A short leg, or a book wide enough that dropping a name is
cheap, is a different experiment and not one the current engine runs.

**The effects themselves are not in doubt.** t of -6.4 on 1,496 sessions and 2,764 cases, matched on two
dimensions, against a bar of 3.63, is not a marginal result. What failed is turning it into a position,
and the gap between those two things is the whole subject of this project.

Related: [[Naive momentum beats it, and the horizon was a fit]] ·
[[The engine was the strategy's biggest short position]] · [[Where the money actually came from]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]] · [[Kronos is confidently wrong]]
