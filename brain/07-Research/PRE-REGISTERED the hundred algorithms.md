---
type: research
tags:
  - research
  - pre-registration
  - execution
  - arbitrage
  - options
  - machine-learning
date: 2026-10-07
status: pre-registered
---

# PRE-REGISTERED the hundred algorithms

Written before anything is built. The owner asked for a list of 100 "algorithms" to be built, implemented and tested.
They are five different kinds of thing, and each is tested the way its kind can honestly be tested. Nothing here
places an order: execution algorithms are **simulated** on recorded market data.

## Not built

* **Market manipulation** (SEBI PFUTP): #34 quote stuffing, #36 ping/flash orders to probe other participants, #16
  bait orders to flush hidden liquidity, #39 evading the exchange's order-to-trade penalties, #100 the MEV sandwich
  (front-running other people's orders). Not written, not simulated.
* **No venue for it in India:** #12 dark-pool aggregator, #15 Nighthawk, #82 dark-pool cross sweeper, the dark half of
  #17, and #14 / #20 which presuppose them - Indian equities trade on lit exchanges only. Crypto and DEX strategies
  (#43, #96 as a crypto tactic, #97, #98, #99) are outside an Indian-equity system. #53 convertible-bond arbitrage: no
  liquid convertible market here.
* **No data to test it with** (listed with what would make it testable): high-frequency and market-making strategies
  #31-33, #35, #37, #38, #40, #84 (co-located tick-by-tick order-book data); option strategies on real quotes #86-91,
  #93-95 (an options price history - nightly chain snapshots began 2026-10-05); #76 index-rebalancing (historical
  index membership); #46 ETF arbitrage (intraday iNAV); #54 merger arbitrage (a deal database); #50 calendar spreads
  (next-month futures history). #67's reinforcement learning is built (below) but only on the simulator.

## Group 1 - execution algorithms, simulated on 55 million Kite minute bars (300 names, 2024-09 to 2026-10)

Parent orders: buys of 0.5%, 2% and 10% of a name's average daily volume, started at 09:30 on every 10th session for
every name, to be finished by 15:15. Market model, deliberately hard on passive orders: aggressive child orders fill
at the minute's typical price plus a half-spread by liquidity bucket plus square-root impact on the child's share of
that minute's volume; a resting limit fills only if the minute trades **through** its price by a tick, and then only
for 10% of that minute's volume; whatever is unfilled at 15:15 is bought at the last price (completion cost).

Algorithms: immediate (market at arrival), TWAP (#2), randomised TWAP (#96 as an equity variant), VWAP on a volume
curve from the previous 20 sessions (#1), target volume - the curve rescaled by volume seen so far (#8), POV 5% and
10% (#3), time-variant POV (#9), target size (#10), Implementation Shortfall with an Almgren-Chriss front-loaded
schedule (#4), adaptive shortfall (#66), market-on-open (#6), market-on-close at the 15:00-15:30 VWAP that sets the
NSE close (#5), minimal impact (#7, POV 2%), time-of-day blocks (#81), a volume-spike pause (#83), iceberg / reserve
(#11), primary, midpoint, offset and market pegs (#21-24), a reverse peg that steps back after adverse moves (#25),
discretionary (#26), laddering (#28), sweep-to-fill (#18), IOC bursts (#19), the lit part of SOR (#17: NSE vs BSE by
the better of the two closes is all the data allows), queue-jump (#27, a tick inside), a Bayesian pegger that learns
the fill rate of each offset (#75), K-means liquidity clusters choosing the best algorithm per cluster on the
training year (#74), a random-forest classifier choosing the algorithm per order from its features (#69), a genetic
optimiser of schedule parameters on the training year (#70), and tabular Q-learning over (time left, inventory left,
recent return) (#67). Train 2024-09 to 2025-08, **test 2025-09 to 2026-10** for every learned one.

Measured per algorithm and size: implementation shortfall against the arrival price (bp, mean and spread), against
the day's VWAP, the share filled passively, and the completion cost.

* **X1** For 0.5%-of-ADV orders, immediate execution is within 3 bp of the best algorithm: impact is small and waiting
  only adds price risk.
* **X2** For 10%-of-ADV orders, schedule algorithms (VWAP, TWAP, POV, IS) beat immediate execution by more than 15 bp.
* **X3** Passive pegs show adverse selection: their filled orders do worse afterwards than their unfilled ones, and
  with completion cost included no peg beats VWAP by more than 3 bp.
* **X4** No learned execution algorithm (#67, #69, #70, #74, #75) beats the best fixed algorithm by 2 bp out of sample.

## Group 2 - technical and position-management rules as complete trading systems

Daily, the liquid universe 2016-2026, long or flat per name, the delivery round trip charged on every change of
position, equal-weighted across names, against the equal-weighted universe; held-out 2022-2026: MA cross (#56), EMA
ribbon (#57), MACD cross (#58), Bollinger reversion (#59), RSI exhaustion (#60), stochastic cross (#61), ATR breakout
(#62), Ichimoku breakout (#63), Donchian (#64), CCI trend (#65), volume-spike entry (#83), a trailing stop (#30) and a
stop-limit exit (#29) on the momentum book. On the Nifty 50: scale-trader (#77), grid (#78), martingale (#79),
anti-martingale (#80), VIX mean reversion (#92), circuit-band events (#85: names closing at a 5/10/20% limit, and
the next sessions).

* **R1** No technical rule beats holding the same universe after costs in 2022-2026 with t >= 2.
* **R2** Martingale shows the largest drawdown of any rule, and its ruin probability under a capital cap is above 10%.

## Group 3 - spreads and arbitrage

Pairs by distance (#41) and by cointegration (#42, Engle-Granger with the test computed here), PCA statistical
arbitrage (#44, residuals of 15 factors, mean-reversion entry), theoretically long-short with the short leg as a stock
future (F&O names only - today's F&O list, a stated survivorship bias); NSE-BSE closing-price gaps against the cost of
crossing them (#47); and, **if Kite serves continuous futures history after the owner's login**, cash-and-carry and its
reverse (#48, #49, #55) and index arbitrage on the Nifty future (#45), net of the futures and delivery costs.

* **A1** Pairs and PCA spreads earn a positive gross return but under 0.1% a week after costs, and do not clear t 2
  in 2022-2026.
* **A2** NSE-BSE closing gaps larger than the cost of crossing them occur on under 1% of name-days among liquid names.
* **A3** Cash-and-carry earns about the risk-free rate (5-8% a year annualised), not more, after costs.

## Group 4 - more learners on the deep feature library

In the walk-forward harness already used for the LightGBM model (same panel, same embargo, same books and costs):
random forest (#69's learner), linear SVM (#71), a neural network (#68), an LSTM over 20-step sequences (#72), a
genetic search over feature weights trained on each year's past (#70), and a regime classifier that chooses between
the momentum and the reversal book each month (#69). NLP sentiment (#73) was tested already
([[Result of the news classifier on filings]]) and is referenced, not rerun.

* **M1** None beats the LightGBM challenger in 2022-2026 by more than 0.10% a month; none beats momentum with t >= 2.

## Group 5 - options strategies, synthetic

No options history exists, so the Nifty 50's own path and India VIX (as the implied volatility) price options with
Black-Scholes - no skew, no real quotes, labelled **synthetic** everywhere: short at-the-money straddle to expiry
(#89 rolling / #93 theta), iron condor (#88), short strangle, long straddle delta-hedged daily (#86 / #87 / #52: it earns
realised minus implied variance), and VIX mean reversion as a Nifty timing signal (#92). Costs: option STT on the sell
premium, Rs 20 brokerage a leg, 1% of premium as slippage.

* **O1** Selling volatility (straddle, strangle, condor) earns a positive average with a few large losses (negative
  skew); the delta-hedged long straddle loses on average (implied above realised).
* **O2** Any synthetic result is an upper bound on what real quotes would give, and none is used by the brain until a
  real options history exists.
