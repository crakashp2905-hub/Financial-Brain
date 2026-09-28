---
type: research
tags:
  - research
  - attribution
  - method
  - paper-trading
date: 2026-09-28
---

# Where the money actually came from

The h60 run returns **+575.32%** over eleven years at Sharpe +1.18. That number says nothing about
whether the strategy works, because most of it is the Indian equity market going up. This is the
decomposition, and it is an identity rather than an estimate: residual **1.02e-14**.

    component                     log    as return   arithmetic
    market                    +1.2265     +240.91%     +136.51%
    universe_selection        +0.1012      +10.65%      +16.89%
    stock_selection           +0.8008     +122.73%      +73.71%
    costs                     -0.2218      -19.89%      -22.15%
    ------------------------------------------------------------
    TOTAL                     +1.9067     +573.05%     +204.96%
    compounding interaction (not a component)          +368.10%

**The stock picking is the second-largest term and it is real.** +0.80 in logs, against costs of
-0.22. The equal-weight tilt over the cap-weighted index - which is free and requires no signal - is
worth only +0.10. So the ordering is: the market gave most of it, the signal gave the next largest
piece, brokerage took a fifth of what the signal produced, and holding equal-weight rather than
cap-weight contributed almost nothing.

## Why the decomposition is in logs

Per session the algebra is exact:

    r_book = r_bench + (r_univ - r_bench) + (r_gross - r_univ) + (r_book - r_gross)

but returns compound rather than add, so summing daily arithmetic contributions does not reproduce the
compounded total. Look at the size of that failure over eleven years: the arithmetic column totals
**+204.96%** against a compounded **+573.05%**, a gap of **+368.10%** that belongs to nobody. Every
arithmetic attribution either smooths that across the components - Cariño, Menchero - or leaves it as a
residual, and over a decade it is larger than every component except the market.

Logs are additive in both directions at once, across components within a session and across sessions,
because each term is a ratio. That is why the log column is the one to read and why `residual` is
1e-14 rather than a number with an excuse attached.

## The bias that was hiding three quarters of the signal

The first version of the universe leg averaged each session's cross-sectional returns and chained
that. It is the obvious construction and it is the same **rebalancing bonus** that was already
rejected once for the control in
[[The engine was the strategy's biggest short position]]: compounding a daily arithmetic mean harvests
the cross-sectional variance of 1,700 names at zero cost, so it reports a universe nobody could hold.

    universe, daily cross-sectional mean chained   +583.33%
    universe, formed equal-weight and HELD          +277.23%

A stock-selection term measured against the inflated bar was understated by nearly all of itself:

    universe_selection   stock_selection
    +0.6954              +0.2067     <- daily mean, wrong
    +0.1012              +0.8008     <- held, correct

Under the wrong version the signal contributed **less than the costs it paid**, which would have been
the headline finding and would have been an artifact. The universe is now formed equal-weight at each
rebalance and held, with share counts fixed and weights drifting as a real book's do.

**One gap remains and it lands on this term.** The attribution's universe compounds to +277.23% while
the engine's control says +328.76%. Both are period-chained; they differ because the control chains
period returns over names priced at both ends while the attribution chains daily values of names
anchored at the period open, so delistings inside a period are handled differently. About 50 points
over eleven years, roughly 1% a year, and whatever it is comes out of `stock_selection`. So read
+122.73% as that figure less up to fifty points of definitional slack - still comfortably the second
term, still comfortably above costs.

## It is a distribution, not three bets

The cheapest available check on whether a result is a strategy or an anecdote, and nothing in this
project asked it before:

    1,299 names traded, 769 winners, hit rate 59.2%
    gross gain Rs 962.32 cr   gross loss Rs -367.52 cr

    top 1  name       1.8% of gains
    top 3  names      5.2%
    top 5  names      7.1%
    top 10 names     10.9%
    127 of 769 winners produced half the gains

The best single name contributed **1.8%** of the gains and the best ten contributed **10.9%**. Half the
gains took 127 names. A run whose top three names produce most of the gains is three bets with a
Sharpe ratio attached; this is not that, and it is the strongest thing anyone has been able to say
about this strategy so far.

The largest positions are small in trade count - the best name made Rs 17.70 cr in **two** trades -
which is what a long holding period looks like rather than a concentrated bet.

## What this does not say

**Costs are measured, slippage is not.** The run used close fills, so shortfall is reported as *not
measured* rather than as zero: brokerage was Rs 81.19 cr over 6,324 fills, and execution slippage on
top of that is a separate measurement that needs minute bars. A zero there would be an absence of
measurement dressed up as an absence of cost.

**h60 was selected on this same window.** The attribution explains a result that is still in-sample,
and explaining an in-sample result convincingly is not the same as the result being real.

**The market term is not a benchmark comparison.** +240.91% is what Nifty 500 did. The strategy beat
it, but so did holding the liquid universe equal-weight, and the honest comparison is against the
universe, which is what `excess_over_universe` reports and what the engine's floor calibrates.

Related: [[The engine was the strategy's biggest short position]] · [[The control]] ·
[[Kronos is confidently wrong]] · [[The cost number that decided everything]] ·
[[Beating the median is not an edge]]
