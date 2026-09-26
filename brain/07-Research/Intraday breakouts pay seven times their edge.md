---
type: research
tags:
  - research
  - strategy
  - intraday
  - firewall
date: 2026-09-26
---

# Intraday breakouts pay seven times their edge

The four strategies excluded from [[Time-series strategies]] for want of minute data are
now tested. Kite supplied **18.45M minute bars** across 100 liquid names over two years,
and [[h15]] was pre-registered with an explicit prior that all four would fail.

They did, and the arithmetic is worth keeping because it is unusually clean.

## The result

| system | fires | gross/trade | net/trade | cost ÷ edge |
|---|---:|---:|---:|---:|
| opening_range (reference) | 60% | +0.043% | -0.666% | **16x** |
| dual_thrust | 17% | **+0.096%** | -0.613% | **7x** |
| r_breaker | 9% | +0.060% | -0.649% | 12x |
| dynamic_breakout_ii | 7% | +0.028% | -0.681% | 25x |

Long leg only, squared off at the session close, each trade paying a full round trip at
the pessimistic `mid` bound of 70.9 bps.

## The edge is real. It is an order of magnitude too small.

Every system earns something gross. The opening-range effect **exists** in Indian equities -
that is a positive finding, and it is the first time this project has measured a gross
edge that was not an artifact.

It is also hopeless. A round trip costs 0.71%, and the largest gross edge on offer is
0.096%. Seven times. No parameter search closes a sevenfold gap, which is why none was
attempted and why h15 fixed that position before the numbers arrived.

## The elaboration works, and that is the interesting part

`dual_thrust` fires on 17% of sessions against the plain reference's 60%, and earns
**+0.096% against +0.043%**. The extra machinery does exactly what it claims: it selects
better trades and fewer of them, more than doubling the per-trade edge.

So the honest summary is not "these systems do not work". It is: **they work, and the
market they were built for is not this one.** They come from the futures literature, where
a round trip is a fraction of a basis point. Indian cash equity pays **20 bps of STT
alone**, before any spread or impact. The measured gap is the size of that mismatch.

## Corroboration that the implementation is faithful

Win rates cluster at **21-22%** across all four. That is precisely the shape an
opening-range breakout should have - many small losses at the 1% stop, a few large
winners - and it is the same right-skewed payoff seen in the event base rates. An
implementation bug would be unlikely to reproduce the textbook distribution.

`ghost_trader` was not run separately. As a modifier it can only reduce the trade count,
and with a gross edge an order of magnitude below cost, trading less often approaches zero
from below rather than crossing it.

## What this closes

The largest genuinely unexplored space in the project is now explored. Minute bars were
the concrete reason to connect Kite, the connection worked, the data arrived, and the
answer is negative and clear.

It also settles something broader: **cost is not a detail of Indian equity strategy, it is
the whole problem at short horizons.** Slow trend died at 70% turnover a *period*; these
die at one round trip a *session*. The two results are the same finding at opposite ends
of the frequency range.

Related: [[h15]] · [[The cost number that decided everything]] ·
[[A hundred strategies and the data that blocks half of them]] · [[Time-series strategies]]
