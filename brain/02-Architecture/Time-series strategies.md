---
type: concept
tags:
  - concept
  - backtest
  - strategy
updated: 2026-09-23
---

# Time-series strategies

Everything this project measured before 2026-09-23 was a **factor**: score every name,
rank them, hold the top fifth, rebalance. That shape covers momentum, value, news flow and
the rest, and the [[Alpha Validation Firewall]] was built around it.

It does not cover the other large family of published strategies - Donchian breakouts,
moving-average crossovers, Bollinger reversion, Faber's trend filter - which are
**stateful**: a name is bought when a condition fires and held until a *different*
condition fires, with no ranking anywhere. A quintile test cannot express "in since the
20-day high broke, out when the 10-day low breaks".

So those strategies were not rejected here. They were **unevaluable**, which is a
different and less honest position to be in.

## How a strategy is written

Two SQL predicates over a windowed OHLC panel, with the position between them carried by
the state-machine idiom:

```sql
last_value(CASE WHEN entry THEN 1 WHEN exit THEN 0 END IGNORE NULLS)
    OVER (PARTITION BY lineage ORDER BY d ROWS UNBOUNDED PRECEDING)
```

## Three decisions that make the numbers mean something

**Execution lags a session.** A signal from a close cannot be filled at that close. The
alternative is common in published backtests and buys a free day of hindsight per trade.

**Highs and lows are adjusted too.** `close_adj = close_raw x factor`, so the same factor
adjusts the high and the low. A breakout run on unadjusted highs fires on every split.

**Cash counts against the benchmark.** A strategy flat through a rally earned nothing
while the universe earned something. Scoring only the invested sessions is how a system
that sits out most of a decade is made to look good.

## What it can and cannot test

Of the fourteen backtests in `letianzj/QuantResearch` (MIT), **five** are testable here.
The exclusions are data facts, not opinions:

| Excluded | Why |
|---|---|
| dual_thrust, r_breaker, ghost_trader, dynamic_breakout_ii | intraday, keyed off the opening range - they need minute bars, and this archive is daily |
| comdty_roll, comdty_spread_roll | commodity futures roll yield; there is no futures curve here |
| portfolio_optimization | an allocator, not a signal - it needs a return forecast to allocate over |
| buy_hold | already [[The control]], and the benchmark every number is net of |

The four intraday systems are the strongest concrete argument for connecting
[[Kite readiness]]: its historical API serves minute bars, which would make them testable
for the first time.

## It shares the trial ledger

A strategy tested here is one more trial against every factor ever tested, and vice versa.
Two counters would let the same search run twice and be reported as two independent
discoveries.

Results: [[The golden cross and the bar it did not clear]].
Bugs it shipped with: [[The universe returned 84% a year]].

Related: [[Alpha Validation Firewall]] · [[MOC Strategies]] · [[The control]]
