---
type: research
tags:
  - research
  - replication
  - technical
  - validation
date: 2026-10-01
---

# Four edges that replicate, and the chart patterns that do work

Every edge this project has found died the same way: a swept horizon worth +246% in sample returned
-7.49% out of sample, an event filter worth +82.9 points cost 4.53. Both had cleared whatever single
threshold was in front of them.

**Replication across independent subsamples is a stronger test than a higher threshold, and the
arithmetic says by how much.** Under the null each subsample's t is standard normal, so the same sign
in k of them at |t| >= m has joint probability `2 * Phi(-m)^k`:

    four tiers, each |t| >= 2.0, same sign       p = 5.4e-07
    one test at t = 3.72 (Bonferroni over 239)   p = 2.0e-04

The conjunction is **370 times stronger**, and it asks a different question: not "is this unlikely under
the null" but "is this the same phenomenon in four places". A number fitted to one subsample has no
reason to reappear in another.

## The subsamples, and an honest substitution

`index_constituents` is **empty** - this database has levels for about a hundred Nifty and BSE indexes
and no membership - so a tier cannot be "the stocks in Nifty Midcap 150". The tiers are point-in-time
turnover ranks that approximate those families:

    large   ranks   1- 50     ~ Nifty 50
    next    ranks  51-100     ~ Nifty Next 50
    mid     ranks 101-250     ~ Nifty Midcap 150
    small   ranks 251-500     ~ Nifty Smallcap 250

Arguably the better instrument for this job anyway: real index membership carries inclusion and
exclusion flows, so a signal measured across a rebalance boundary partly measures index funds trading
the boundary.

The statistic is a cross-sectional rank correlation computed **inside a tier on a single session**, so
the market factor is differenced out - a day when everything rose contributes nothing to any tier. That
is what makes the tiers close to independent and the conjunction meaningful.

## Four of twenty-two survive

    feature            large            next             mid            small       joint p
    vol_60        -0.0330/ -5.95  -0.0330/ -6.43  -0.0558/-14.19  -0.0678/-19.45   6.1e-36
    mom_12_1      +0.0235/ +4.55  +0.0378/ +8.28  +0.0395/+11.27  +0.0527/+17.57   9.6e-23
    dist_52w_high +0.0156/ +3.14  +0.0358/ +7.64  +0.0502/+13.34  +0.0671/+19.68   1.0e-12
    atr_pctile_250-0.0377/ -9.48  -0.0081/ -2.09  -0.0277/-11.27  -0.0299/-13.48   2.2e-07

All four survive multiplicity correction by the 22 features tried, the weakest at an adjusted
p of 4.9e-06.

**Three have a monotone size gradient** - the IC rises steadily from large to small, momentum from
+0.024 to +0.053, 52-week-high from +0.016 to +0.067. A fitted number does not produce a clean
gradient across four independently measured subsamples. That structure is the strongest evidence here
that these are real effects rather than survivors of a search.

**But four survivors are two ideas.** `vol_60` and `atr_pctile_250` are both volatility; `mom_12_1` and
`dist_52w_high` are both momentum. `evaluation/families.py` already classifies them that way. So the
finding is: **low volatility wins and high momentum wins, each confirmed by two correlated
measurements, in every size tier.** Both are decades-old published anomalies. The contribution here is
not discovering them - it is that they are the *only* things in a twenty-two-signal technical toolkit
that survive this test on Indian equities.

## Everything a chart trader reaches for fails, and fails informatively

RSI, stochastic, Williams %R, Bollinger %, ADX, CCI, MFI, OBV slope and the MACD histogram all fail -
and most fail by **flipping sign between tiers**:

    rsi_14   large -3.36   mid +2.13
    adx_14   large -4.25   next +5.60   mid -1.49

**ADX reaches |t| = 5.60 in the `next` tier alone.** The single-test Bonferroni bar over the whole
ledger is 3.68. A conventional study on Nifty Next 50 would have reported ADX as a discovery clearing
multiple-testing correction - and it has the opposite sign in the tier immediately above and below. That
one row is the clearest argument in this project for why replication is the test.

## The candle patterns do carry something, measured correctly

Six of seven candle patterns were **unmeasurable** by the tier method, and the reason is instructive:
they fire on 0.22% to 1.5% of rows. A fifty-name tier holds zero or one hit on a typical session, so a
within-session rank correlation has nothing to correlate. **Candle patterns are not cross-sectional
signals.** They are sparse per-name events, and the right instrument is the matched event study built
for filings - control drawn from the same session and the same momentum *and* recent-return bucket.

Pointed at `candles` instead of `event_flags`, against a Bonferroni bar of **3.695**:

    pattern                   h    effect       t   sessions    cases
    k_bearish_engulfing       5    -0.62%   -6.36      2,154   15,835   CLEARS
    k_marubozu_bull           5    +0.95%   +6.28      2,388   18,326   CLEARS
    k_shooting_star           5    -0.47%   -3.77      2,373   18,704   CLEARS
    k_shooting_star          20    -0.64%   -3.70      2,373   18,686   CLEARS
    k_marubozu_bull          20    +0.75%   +2.98      2,387   18,289
    k_morning_star           20    -0.56%   -2.78      1,942   12,414
    k_bearish_engulfing      20    -0.56%   -2.71      2,153   15,813
    k_hammer                  5    +0.39%   +2.55      1,855    8,595

    8 of 14 significant at nominal 5%, where chance predicts 0.7

**The signs are what a chart trader would tell you.** Bearish engulfing is bearish. Marubozu bull is
bullish. Shooting star is bearish at both horizons. Hammer is bullish. Doji - the indecision candle -
shows nothing at all, which is the correct answer for a pattern that claims no direction. Only morning
star has the wrong sign.

That coherence across seven independent patterns is not something a data-mining artifact produces. It
is the first time this project has found technical chart patterns to carry information, and the reason
nobody found it before is that they were being tested as cross-sectional factors when they are events.

## What this does not yet say

**The candle effects are strongest at h5 and decay by h20.** Marubozu bull: +0.95% at five sessions,
+0.75% at twenty. A round trip costs roughly 80 bp, so +0.95% gross is about +0.15% net per occurrence -
positive, thin, and at a five-session horizon the turnover is exactly what
[[Naive momentum beats it, and the horizon was a fit]] showed the cost model will not pay for. These are
signals, not yet a strategy.

**None of it is out of sample.** 2016-04 to 2026-06 throughout. Replication across *subsamples* is
orthogonal evidence to replication across *time*, and the second has not been run. That is the obvious
next experiment and it should be pre-registered: take the four tier-replicated features and the four
candle patterns exactly as they stand, and test them on a window none of this touched.

**The tiers are a proxy.** Turnover rank, not index membership, because `index_constituents` is empty.
Fetching real NSE/BSE constituent history would let the same test run on the actual index families, and
would also answer whether the size gradient is a size effect or a liquidity effect.

Related: [[Naive momentum beats it, and the horizon was a fit]] ·
[[The event archive is real and it is not tradeable]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]] ·
[[Charts, candles and indicators]] · [[Intraday candles and the bid-ask bounce]]
