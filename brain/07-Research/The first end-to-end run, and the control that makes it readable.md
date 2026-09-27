---
type: research
tags:
  - research
  - paper-trading
  - method
date: 2026-09-27
---

# The first end-to-end run, and the control that makes it readable

`paper/engine.py` closes the chain: hypothesis, point-in-time signal, order, fill walked through
real minute volume, position, cash, mark to market, P&L. The distinction it exists to enforce is the
one between research and a trading system - *signal today, buy today's close, compute the future
return* against *signal at t, order at t+1, fill from the volume that traded* - and the difference
is that the second one actually holds something.

## EXP-b1b67f761db8

`dist_52w_high`, top 20 by signal, monthly rebalance, Rs 1 crore, 2024-10-01 to 2026-09-18,
restricted to the 100 names with minute bars so every fill is measured:

    sessions               488        trades                 480
    rebalances              25        simulated fill share   100%
    total return        +41.75%       unfilled orders           0
    volatility          17.96%       cash never negative     yes
    Sharpe               +1.088      equity identity held    488/488
    max drawdown        -20.41%       stale marks               0
    costs             Rs 407,691      cost drag              4.08%

## And the control, which is the only reason that table means anything

    strategy                                            +41.75%
    equal-weight buy-and-hold of the same 100 names      +22.65%
    Nifty 500 over the same window                        -5.89%
    ------------------------------------------------------------
    excess over the traded universe                      +19.11%

**The universe beat the market by 28.5 points before any strategy touched it.** Read the +41.75%
without the +22.65% and two thirds of it is universe selection.

Worse: that universe is selected with **hindsight**. `ingest/kite_minute.liquid_universe()` picks the
top names by median turnover *as computed when the ingest ran*, in September 2026 - and names that
performed well over 2024-2026 attract volume. The 100 names are therefore partly a list of what went
up.

So the honest figure is the **+19.11% excess over the traded universe**, about **+9.1% a year**.

## Which agrees with a completely different measurement

[[Asking the question the factor actually claims]] put `dist_52w_high`'s Jensen alpha against Nifty
500 at **+0.705% per 20 sessions, roughly +9% a year**, from a cross-sectional quintile regression
over eleven years.

A walk-forward paper engine with measured fills over two years, and a cross-sectional alpha
regression over eleven, arrive at the same number by routes that share almost no code. That is the
first corroboration of anything in this project and it is worth more than either measurement alone.

It is not a pass. Three things stand between it and one:

**The universe is still hindsight-selected**, and the *excess* is not clean either: if those 100
names were chosen partly for having gone up, the ones that did best within them may be
systematically the ones that trended - which is exactly what `dist_52w_high` buys.

**Twenty-five independent decisions.** 488 daily observations flatter the Sharpe; the strategy made
25 rebalance choices.

**No out-of-sample window.** 2024-10 to 2026-09 *is* the minute-bar coverage. There is nothing held
back, and [[h20]] still needs calendar time.

## Two bugs found by insisting on the control

Both made the result look better, and both were found by asking a question rather than by care.

**The target book was intersected instead of ranked.** `_target_book` took the global top 20 of
~1,700 names and *then* filtered to the 100 with minute bars. The top 20 rarely includes one of the
100, so the book held **two to four names**, and the run returned -12.52% at 33.7% volatility with a
**-46.2% drawdown**. Those are the numbers of a concentrated bet, not of a strategy, and reporting
them as "the strategy lost money in paper trading" would have been as wrong as reporting the +41.75%.
`eligible` now restricts the ranking universe, because "this strategy on this universe" means ranking
within it.

**The decision price was the fill session's own close.** So implementation shortfall measured the
intraday drift between the open and that close rather than any slippage, and reported **-26.8 bps, a
gain**, on 480 fills. Shortfall is achieved-against-decision and the decision was made *yesterday*;
it now uses the signal session's close.

## The control now lives inside the engine

`evaluation/control.py` made this project's most important early finding by asking exactly this
question of the committee - *the universe returned +5.00%, the committee contributed -1.85%* - and I
walked into the same trap because the control lived in a different module and nothing obliged anyone
to run it.

Every `Run` now carries `universe_buy_and_hold`, `benchmark_return`, `excess_over_universe` and
`excess_over_benchmark`. The difference between a control and a good intention is whether it can be
skipped.

Related: [[h20]] · [[Asking the question the factor actually claims]] ·
[[Execution was not where the edge died]] · [[MONEY GATE]] · [[The control]] ·
[[Beating the median is not an edge]]
