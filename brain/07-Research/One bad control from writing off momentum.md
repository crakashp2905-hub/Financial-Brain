---
type: research
tags:
  - research
  - firewall
  - method
date: 2026-09-27
---

# One bad control from writing off momentum

The cross-sectional tests had never been run against a null, because the null database carried
`adjusted_prices` and `eod_prices` and no `features` table. Adding one produced a result that
looked conclusive and was wrong.

## What the first null said

| factor | h | net t real | net t null | separation |
|---|---:|---:|---:|---:|
| `mom_12_1` | 20 | +2.63 | **+2.67** | **−0.05** |
| `mom_12_1` | 40 | +2.85 | +2.30 | +0.55 |
| `dist_52w_high` | 20 | +1.59 | −1.04 | +2.63 |

Twelve-minus-one momentum scored **higher on "pure noise" than on real data**. Read at face
value that retires the most replicated anomaly in the literature - Jegadeesh & Titman (1993),
confirmed out of sample by the same authors in 2001, internationally by Rouwenhorst (1998) and
Griffin, Ji & Martin (2003), and Carhart's (1997) fourth factor.

## Why it was wrong

`synthetic.build` gives each synthetic name **the drift of the real name it was matched to**:
`rng.gauss(mu, sigma)` with per-name `mu`. Persistent per-name drift *is* cross-sectional return
persistence, and cross-sectional return persistence is the definition of momentum. The panel
contained the alternative hypothesis. A momentum ranking on it correctly identified the
high-drift names, which then kept drifting, by construction.

Per-name drift is the *right* choice for a **timing** rule, which trades one name over time and
wants a realistic spread of volatilities to work against. It is disqualifying for any
**cross-sectional** test. The same control is correct for one question and plants the answer to
another, which is not a distinction the module made.

`drift="common"` gives every name the pooled mean drift and keeps its own volatility, so
cross-sectional differences are pure noise.

## The fix is confirmed by what happened to the IC

| `ic_t` on noise | per-name drift | common drift |
|---|---:|---:|
| `mom_12_1` | **+4.01** | **+0.20** |
| `dist_52w_high` | +3.81 | +1.24 |
| `above_ma200` | +3.25 | +1.56 |
| `ret_20d` | −2.41 | −1.03 |

A rank IC of +4.01 on a panel with no information was the tell, and it was there to be read
before the separation table was believed.

## Separations against a null that is one

| factor | h | net t sep | alpha t sep |
|---|---:|---:|---:|
| **`dist_52w_high`** | 40 | **+4.56** | **+5.16** |
| **`dist_52w_high`** | 20 | **+3.96** | **+5.04** |
| **`mom_12_1`** | 20 | **+3.09** | **+3.12** |
| **`mom_12_1`** | 40 | **+2.75** | **+2.73** |
| `vol_60` | 40 | +2.35 | −0.44 |
| `vol_60` | 20 | +1.28 | **−2.76** |
| `above_ma200` | 20 | +0.93 | **−0.00** |
| `ret_20d` | 20 | −3.58 | **−4.48** |

**Momentum's separation moved from −0.05 to +3.09 purely by specifying the null correctly.**

Three other things settle at the same time. `vol_60`'s alpha separation is **−2.76**: it scores
better on noise than on real data, so low volatility contains nothing on its own criterion -
the same conclusion [[Asking the question the factor actually claims]] reached by regression,
now reached independently. `above_ma200`'s alpha separation is **−0.00**, exactly nothing, after
[[h11]], [[h12]] and [[h13]] were spent chasing a turnover figure that
[[The tie-break that cost three hypotheses]] showed was mostly phantom. And `ret_20d` separates
at **−4.48** - short-term reversal is decisively worse than noise.

## Separation is not significance, and both are needed

Separation answers **"is there anything here"**. The Bonferroni bar answers **"did this make
money provably"**. Both candidates now pass the first and fail the second:

    dist_52w_high  h=20   alpha +0.705% vs Nifty 500   t +3.12   bar 3.59   sep +5.04
    mom_12_1       h=40   net   +1.202% per rebalance   t +2.85   bar 3.59   sep +2.73

## Stated against the result

The null panel holds **300 names against the real panel's ~1,000** per rebalance, so its
quintiles are built from fewer names, its t-statistics are noisier, and the separations above
are biased upward by some amount this has not measured. The rebalance counts match exactly
(n = 132 at h = 20, n = 65 at h = 40), so the comparison is aligned in time - but a
same-width null is the stricter test and has not been run.

Related: [[Running the strategies on noise]] ·
[[Asking the question the factor actually claims]] ·
[[The horizon was the binding constraint]] · [[The tie-break that cost three hypotheses]]
