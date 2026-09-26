---
type: research
tags:
  - research
  - firewall
  - method
date: 2026-09-27
---

# Asking the question the factor actually claims

Every gate in this project asked a long-only quintile one question: **did it out-earn the
equal-weighted universe in raw percentage terms?** For momentum that is nearly the right
question, because a momentum book carries a beta close to one and raw excess and alpha come to
almost the same number. For a factor whose claim is about *risk* it is the wrong question, and
the harness had been rejecting low volatility on a criterion the anomaly never claimed to meet.

`vol_60` has the strongest rank IC of anything measured here - **t = +12.30** at a two-session
horizon - and its lowest-volatility quintile's raw excess is negative at every horizon tested.
Both are correct. Haugen & Heins (1975), Ang, Hodrick, Xing & Zhang (2006), Blitz & van Vliet
(2007) and Frazzini & Pedersen (2014) all claim low-beta assets earn *more than their beta
justifies* - a positive intercept, not a higher raw return.

`evaluation/riskadjusted.py` measures the intercept: Jensen's (1968) alpha with Newey-West
standard errors, net of the book's own per-name costs.

## The answer for low volatility is no, and now for the right reason

| factor | h | beta vs Nifty 500 | alpha | alpha t |
|---|---:|---:|---:|---:|
| `vol_60` | 20 | 0.908 | −0.029% | −0.31 |
| `vol_60` | 40 | 0.934 | −0.013% | −0.07 |
| `vol_20` | 20 | 0.973 | −0.292% | −2.42 |
| `atr_14_pct` | 40 | — | −0.359% | −1.29 |

**Alpha is negative and beta is not low.** Against the cap-weighted market the
lowest-idiosyncratic-volatility quintile carries a beta of **0.91 to 0.97**. Low residual
volatility in Indian equities does not buy low market beta, which is the mechanism the anomaly
requires. The earlier rejections were right, and they were right by accident.

Corroboration with none of this project's code in the path: NSE publishes
`Nifty500 Low Volatility 50`. Over 2016-2026 it returned **+281% against Nifty 500's +243%**
while carrying **beta 1.08 to 1.15**. Higher return, higher beta, no risk-adjusted gap. (That
series holds only 435 of ~2,500 sessions, so a regression on position-spaced returns from it is
unreliable; the level comparison is the part that holds.)

## The answer for 52-week-high proximity is the strongest result this project has produced

`dist_52w_high` - George & Hwang (2004), how close a name trades to its own 52-week high -
net of per-name costs:

| benchmark | h | beta | alpha | alpha t |
|---|---:|---:|---:|---:|
| equal-weighted universe | 20 | 0.711 | +0.642% | **+5.31** |
| **Nifty 500** | 20 | 0.964 | +0.705% | **+3.12** |
| equal-weighted universe | 40 | 0.716 | +1.328% | +4.83 |
| **Nifty 500** | 40 | 0.958 | +1.445% | **+3.11** |

Against the investable market that is roughly **+9% a year of alpha at market beta**, on a
book turning over 46% a period, after the measured cost of its own liquidity mix.

**It does not pass.** The Bonferroni bar is |t| > 3.54 and rising - these trials are themselves
recorded - and +3.12 is short of it. It is nonetheless the closest anything has come, on the
hardest benchmark, on the criterion the literature states.

`mom_12_1` behaves the way a momentum book should: beta 0.98 against the equal-weighted
universe, so its alpha (+1.24%, t = +3.00) and its raw excess (+1.20%, t = +2.85) agree almost
exactly, and nothing about the criterion change rescues or damages it. Against Nifty 500 its
beta is **1.21** and its t falls to **+1.95** - a bigger alpha with a worse t, because a
momentum book is poorly explained by a cap-weighted index and the residual variance grows.

## The benchmark mattered more than the estimator

Read the beta column twice. Against the **equal-weighted** universe of ~1,000 Indian names,
`dist_52w_high` has beta 0.711 and `vol_60` has 0.621. Against **Nifty 500** the same books have
0.964 and 0.908. The equal-weighted universe is small-cap heavy and far more volatile than any
cap-weighted index, so every book looks low-beta against it and every intercept is inflated by
`(1 - beta) x benchmark return`.

That is a structural defect in the benchmark this project has used for every test, and it
inflates alpha for exactly the factors whose claim is about beta.

## The bug that produced a passing result, recorded because it was beautiful

The first version of the estimator demeaned *x* before fitting, to make the intercept's
Newey-West variance a one-line long-run variance of the residuals. With *x* demeaned the
intercept is **mean(y)** - the quintile's own return over the risk-free rate, with no beta
adjustment whatever.

It reported:

    vol_60        h=40   alpha +1.0447%   t +4.99     <- would have cleared the 3.54 bar
    mom_12_1      h=40   alpha +3.2709%   t +8.78     <- +21% a year
    dist_52w_high h=40   alpha +2.8066%   t +11.29

Every one of those was a rising market being read as skill. The tell was there in the table -
an alpha larger than the raw excess by a factor of three, on a book with beta near one, is
arithmetically impossible - and it was reported before being checked.

The rewrite fits on the original *x* with the full 2x2 HAC sandwich, and it was validated
against planted values before being pointed at data: a synthetic book built at beta 0.600 with
1.00% alpha comes back at beta 0.596 and alpha +1.13% (within 1.5 standard errors), and a book
identical to its benchmark comes back at beta 1.000 and alpha 0.000000%. An estimator that
cannot recover a planted answer is not worth pointing at data, and that check should have come
first rather than second.

Related: [[The horizon was the binding constraint]] ·
[[The toll was measured with one number and it needed thousands]] ·
[[Alpha Validation Firewall]] · [[Running the strategies on noise]] · [[Low volatility]]
