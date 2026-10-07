---
type: research
tags:
  - research
  - result
  - arbitrage
  - futures
date: 2026-10-07
status: result
---

# Result of cash-and-carry and basis trading

Pre-registration: [[PRE-REGISTERED the hundred algorithms]] (Group 3, A3). Kite continuous near-month futures history,
read only, for 219 F&O underlyings (354,860 daily closes, 2016-2026), against each stock's unadjusted spot close.
12,820 stock contracts held from their first session to expiry (209 stocks), plus 128 Nifty 50 contracts. Costs:
0.265% for the round trip (STT on both delivery legs and the futures sale, stamp, exchange, DP charge, brokerage).
Judged against a 6.5% risk-free rate, because a hedged carry is a rate of return, not a bet. t counts one
observation per expiry month (contracts expiring together share one market).

## Every contract, unconditionally

Median annualised basis at the start: **4.9%** - below the risk-free rate. Net of costs the carry earned **0.7% a year**;
it beat the risk-free rate on 15% of contracts; excess over the risk-free rate -0.47% a contract (t -21.5). The Nifty
future: basis 4.4% a year, net 0.6%, excess -0.47% (t -18). The basis does close: the median gap at expiry is 15 bp.

**A3** (cash-and-carry earns about the risk-free rate, 5-8%, not more): **wrong in the safe direction** - it earns
far less. The futures market is not paying a carry worth taking on average.

## Only when the basis is rich (basis trading, #55)

| enter only if the annualised basis exceeds | contracts | net a year | excess over risk-free, per contract | t (by month) | 2022-26 t |
|---|---|---|---|---|---|
| 8% | 2,501 | 6.1% | -0.02% | -0.6 | -0.5 |
| **10%** | **607** | **7.6%** | **+0.14%** | **+2.9** | **+5.2** |
| 12% | 129 | 9.8% | +0.31% | +3.8 | +8.3 |
| 15% | 26 | 13.4% | +0.53% | +3.9 | +6.8 |

When a stock future trades more than 10% a year above spot, buying the stock and selling the future to expiry has beaten
the risk-free rate after costs, in every period and every threshold above 10%, and the worst contract at 10%+ lost
2.0%. **This is the first arbitrage result in this project that survives costs out of sample.** The thresholds were
fixed in the run script before it ran, but no pass criterion was registered for them, so this is recorded as a strong
result to be confirmed live, not as a passed test.

What it is not, stated:

* **Capital:** the spot is bought in full and the short future needs margin (about 15-20% of notional) - the "excess"
  is per rupee of spot; per rupee of total capital it is roughly a fifth lower.
* **Dividends:** the spot holder receives them and the P&L above does not count them, so it is understated where a
  dividend fell inside the contract.
* **Capacity:** rich bases cluster in a few names and months (607 contracts over 90 months); a book can hold only a
  handful at a time, and the far legs are thinner.
* **Why it exists:** rich bases appear when longs crowd into a future (or the stock is in the F&O ban list and new
  positions are restricted) - the arbitrageur is paid for supplying the other side, and sometimes cannot.

## The reverse (#49)

The future traded below spot by more than costs on 19.8% of contracts, but even if the stock could be shorted
(only through securities lending in India), the reverse earned -0.06% a contract on average. Nothing there.

## What follows

A live paper carry book, pre-registered in [[PRE-REGISTERED live paper zoo]]'s addendum below, built from the nightly
F&O snapshot: it enters wherever the annualised basis is above 10% and holds to expiry.
