---
type: research
tags:
  - research
  - opportunity-memory
  - attribution
  - method
date: 2026-09-29
---

# What the book declined, and what it cost

A system that records only its trades learns from a sample it selected itself. It can measure how its
positions did; it can never measure what it declined. So a rule that systematically rejects good
opportunities is invisible to it, because the evidence that would expose the rule is exactly what the
rule threw away.

`opportunity_memory` records every candidate at every rebalance - taken, held, sold, and the far
larger number rejected, with *why* - then resolves what each actually did. The momentum book
(`mom_12_1`, h60, top 100, eleven years) wrote **44,732 candidate rows over 44 rebalances**, of which
42,335 resolved.

    disposition            n     mean excess   win rate   unresolvable
    REJECTED_RANK     36,470          -0.33%      42.1%            150
    EXITED             2,477          +0.26%      31.2%            726
    HELD               2,078          +1.65%      45.8%             11
    TAKEN              1,870          +3.89%      48.3%             16
    REJECTED_CASH        352          +2.28%      45.5%              9

    taken (TAKEN + HELD)      +2.71% over 3,948
    rejected                  -0.30% over 36,822
    REJECTION EDGE            -3.01%

Excess is measured against the mean of everything *considered* on that session, which is the right bar:
the question is not whether a rejected name went up, but whether it went up more than the alternatives
the ranking had in front of it at the time.

**The rejection edge is -3.01%.** The ranking declines the worse names, by three points of forward
excess, over 36,822 rejections. That is the first direct measurement of whether the selection works,
as opposed to whether the portfolio made money - and it works.

## Three things only this table can see

### New positions beat continuing ones by 2.24 points

    TAKEN  +3.89%      entering the book this rebalance
    HELD   +1.65%      already held and staying

A name is at its best in the window right after it enters. The signal decays *inside* the holding
period, which is an argument for shorter holds - and it collides head-on with
[[Naive momentum beats it, and the horizon was a fit]], where h20 cost 150% of opening equity in
brokerage against h60's 81%. The signal wants a shorter horizon and the cost model will not pay for
one. That tension is the whole problem restated, now with a number on each side.

### The cash constraint is costing 2.28% on 352 names

`REJECTED_CASH` is a name the ranking *wanted* and the book could not afford - the buy loop spends
cash in rank order and runs dry before reaching the bottom of the target. Those 352 names returned
**+2.28%** excess, better than everything except new entries.

This is not a strategy decision. It is `int(gap // px)` rounding down and a sequential spend, the same
mechanism behind most of the engine's calibration floor. It is a fixable engineering loss, and until
this table existed there was no way to know it had a price.

### Exits are mostly names that stopped trading

`EXITED` covers 2,477 rows and **726 of them are unresolvable** - the lineage had no price 60 sessions
later. Their win rate is 31.2% against 42-48% everywhere else. Selling is overwhelmingly happening to
names on their way out of the market, which is the book working, and the 31.2% win rate says the
timing is right more often than not.

That split only appeared after a labelling fix. The first version put a held name that ranked out into
`REJECTED_RANK`, pooling every sell with the 36,470 names the book never owned, so `EXITED` meant only
"vanished from the universe" and the one disposition that measures selling decisions measured
delistings instead.

## What the false-negative rate does not mean

    false negatives   15,527   42.2% of rejections
    false positives    2,094   53.0% of acceptances

Both numbers look alarming and neither is. 42.2% of rejected names beat the field, but roughly half of
*any* set of names beats the median by construction - that is what a median is. The number that
carries information is the **rate relative to the acceptances**: rejected names win 42.2% of the time,
accepted names 48.3%, and the six-point gap is the selection skill, consistent with the -3.01%
rejection edge.

Reporting the false-negative count on its own would make a working ranking look broken, which is why
the correct skips are in the denominator.

## What this does not answer

**The portfolio counterfactual.** A rejected name's forward return is a fact about the market, not a
simulation of a trade that never happened. What this cannot say is what the *portfolio* would have
done holding it instead of something else - that needs re-running the book with the name substituted
in, which is a different and much harder experiment.

**Whether any of it is out of sample.** These 44 rebalances are the same 2015-2026 window that
[[Naive momentum beats it, and the horizon was a fit]] walked forward. The rejection edge is an
in-sample measurement of an in-sample book. It says the ranking separated winners from losers over the
period it ran; it does not say it will.

**Whether acting on `REJECTED_CASH` would help.** The obvious response - raise capital, round share
counts up, spend cash proportionally rather than sequentially - is a change that would have to be
pre-registered and walked forward like everything else. Two filters have already died that way.

Related: [[Naive momentum beats it, and the horizon was a fit]] ·
[[The event archive is real and it is not tradeable]] · [[Where the money actually came from]] ·
[[The engine was the strategy's biggest short position]]
