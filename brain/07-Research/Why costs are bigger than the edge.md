---
type: research
tags:
  - research
  - costs
  - explainer
date: 2026-10-04
status: result
---

# Why costs are bigger than the edge

Round-trip cost of a delivery trade in basis points of the amount traded (1 bp = 0.01%), from
`costs/india.py`, identical for Rs 1 lakh, Rs 5 lakh and Rs 25 lakh orders:

    STT (securities transaction tax)   20.0 bp   0.1% on the buy AND 0.1% on the sell, delivery
    exchange transaction charge         0.6 bp
    SEBI turnover fee                   0.0 bp
    stamp duty (buy side)               0.1 bp
    brokerage                           0.0 bp   (zero on delivery at a discount broker)
    GST (on brokerage + exchange)       0.1 bp
    ------------------------------------------
    statutory + broker                 20.9 bp   of which STT is 96%

    market impact + half-spread (the model's assumption, per bucket, round trip)
      mega (top 100)     10 bp      large (101-300)  20 bp
      mid (301-750)      50 bp      small (751-1500) 120 bp

    total round trip   mega ~31 bp    large ~41 bp    mid ~71 bp    small ~141 bp

## The three kinds of cost

**1. A fixed tax: STT, 20 bp.** It is charged on delivery trades both ways, does not depend on size, and
cannot be negotiated away. Every round trip starts 0.2% in the hole before anything else. Intraday
(square-off the same day) pays only 2.5 bp of STT, which is why intraday looks cheaper, but it has to earn
that edge inside a day (it did not: the intraday study found nothing).

**2. Everything else statutory is a rounding error: about 1 bp.** Exchange, SEBI, stamp, GST and zero delivery
brokerage together are smaller than a tick.

**3. Market impact and the spread: the only variable cost, and the only uncertain one.** It is what you pay
for taking liquidity: you buy at the offer and sell at the bid, and a larger order moves the price. The model
scales it by liquidity bucket and by the square root of order size against turnover (the square-root law).
It is an *estimate*. Against it, the minute-bar execution simulator measured about 0 bp of shortfall for
liquid names at Rs 5-25 lakh, but that simulator fills at the minute's typical price and cannot see the
spread, so the truth sits between the two. For a liquid large-cap, plan on roughly 25-40 bp round trip all
in; for a mid-cap, 55-70 bp.

## What it costs a strategy per year

Cost per year = round-trip cost x the number of times the book is replaced:

    monthly rebalance, whole book replaced    12 turns/yr   -> 4.9% a year (large-cap)   8.5% (mid-cap)
    monthly, a third replaced                  4 turns/yr   -> 1.6%                      2.8%
    quarterly, a third replaced               1.3 turns/yr  -> 0.5%                      0.9%
    hold a year                                1 turn/yr    -> 0.4%                      0.7%

## The edge it is up against

    composite expected alpha per name, 20 sessions        about +0.1% to +0.3%, gross
    top-20 composite book, realised, gross                -0.15% a month
    top-20 composite book, realised, after sized costs    -0.52% a month
    momentum sleeve, after costs, monthly                 +0.47% a month   (the one that clears)
    reversal sleeve, after costs, monthly                 -0.84% a month   (turnover kills it)

A signal worth +0.1% to +0.3% a month cannot pay a 0.4% round trip once a month, so the engine's hurdle
(net alpha above 0.5%) is almost never met. The signals with the *slowest* decay (momentum, 52-week high:
information still growing at 120 sessions) are the ones that survive, because they can be traded rarely.
Reversal decays in 16 sessions and has to be traded monthly, so costs take more than all of it.

## What to do about it, in order of how much it moves

1. **Trade less.** Turnover is the lever. Halving turnover halves the cost; it is the only free lever.
2. **Trade liquid names.** Moving a position from mid to large cap saves ~30 bp a round trip.
3. **Hold the signals that last.** Match the rebalance to the half-life, not to the calendar.
4. **Do not pay STT twice for nothing.** A stop or a rebalance that sells and re-buys the same name pays 20 bp
   to end up where it started.
5. **Do not shop for a cheaper broker.** Brokerage on delivery is already zero; the cost is tax and the market.

Related: [[Result of the opportunity engine replay]] · [[Trader layers built, and what they showed]]
