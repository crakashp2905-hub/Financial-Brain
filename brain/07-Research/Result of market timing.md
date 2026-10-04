---
type: research
tags:
  - research
  - market-timing
date: 2026-10-04
status: result
---

# Result of market timing (Nifty 500 price index)

Predictions committed at `1fd9e5e` before running. 2016-06 to 2026-09 (every rule and buy-and-hold on the same
days), signal at the close of t and position from t+1, cash at 6%, 5 bp one way per switch.

    rule              CAGR   vol   Sharpe  max DD  Calmar  in mkt  switches/yr   Sharpe diff vs BH [1st, 99th pct]
    buy and hold     12.7%  16.0%   0.46   -38.3%   0.33
    R1 trend>200d     9.1%  11.3%   0.31   -19.6%   0.46    77%      7.2         -0.17 [-0.73, +0.42]
    R2 50/200 cross   5.4%  12.6%   0.02   -42.3%   0.13    74%      1.9         -0.44 [-0.86, -0.01]
    R3 breadth>=50%  10.9%  10.5%   0.48   -16.6%   0.66    65%      6.1         +0.00 [-0.66, +0.68]
    R4 vol<=1.5x med 11.3%  12.2%   0.46   -19.6%   0.58    85%      4.1         -0.02 [-0.41, +0.47]
    R5 R1 and R3     10.4%  10.3%   0.45   -16.9%   0.62    64%      7.6         -0.04 [-0.70, +0.64]

    A1 R1, R2 cut max DD >= 25% but not significant   PARTLY  R1 yes (-38% -> -20%, Sharpe diff n.s.); R2 FAILS: its drawdown was -42%, worse than buy-and-hold
    A2 no rule has a higher CAGR than buy-and-hold    PASS    all five are lower (5.4% to 11.3% vs 12.7%)
    A3 breadth gives no significant benefit           PASS    Sharpe diff +0.00, interval -0.66 to +0.68
    A4 volatility rule: lower drawdown, n.s. Sharpe   PASS    -19.6%, diff -0.02
    A5 R1 switches >= 4/yr, cost drag < 1%/yr         PASS    7.2 switches a year, CAGR 9.49% free -> 9.10% at 5 bp

**Nothing beats buy-and-hold on risk-adjusted return, and nothing is close to the 99% bar.** What the rules
do is trade return for a shallower drawdown: three of them (trend, breadth, volatility) roughly halve the
worst drawdown, from -38% to -17% to -20%, and give up 1.4 to 3.6 points of CAGR for it. That is insurance
priced fairly, not alpha.

**Where the trend rule earned its keep and where it did not**

    window                buy-and-hold   trend rule R1
    2015-16 correction       -13.6%         +1.9%
    2018 fall                 -3.3%         -2.6%
    Covid crash (2020)       -27.1%         -7.8%
    2022 fall                -15.1%        -17.1%     (whipsawed: out late, in late)
    2024-25 fall              -9.7%         -9.8%

One crash (Covid) provides almost the whole of the drawdown benefit. In four of the five windows the rule
did about the same as holding, and in 2022 it did worse. With a single crash in the sample, the honest
reading is that the 99% test cannot tell a good timing rule from luck, and the point estimates say "a
trend filter is cheap crash insurance, not a way to make more".

**The 50/200 cross is a trap.** It exits so late (the 50-day average crosses below the 200-day only after
the damage) and re-enters so late that its worst drawdown was *worse* than not timing at all.

**Costs are not what sinks timing.** 5 bp a switch costs the trend rule 0.4 points of CAGR a year; even
12.5 bp costs about 1 point. The loss is the return forgone while out of the market (23% of days), not the
fees.

Related: [[PRE-REGISTERED market timing and exit rules]] · [[Result of half-life and second-order spillover]]
