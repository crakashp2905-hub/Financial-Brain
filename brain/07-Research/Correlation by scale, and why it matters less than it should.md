---
type: research
tags:
  - research
  - risk
  - method
date: 2026-09-27
---

# Correlation by scale, and why it matters less than it should

`risk/covariance.py` computes one correlation matrix from daily returns over 250 sessions, and every
number downstream inherits it: portfolio volatility, risk contributions, effective bets, the
correlation limit in the portfolio gate. The assumption buried in that is that **correlation is a
single number**, and it is not. Two names can move together over quarters and be uncorrelated day to
day, and a book held for forty sessions cares about the former.

Morlet wavelets are the right instrument for the question, because unlike Fourier they localise in
time as well as frequency, and financial series are not stationary. `features/wavelet.py` implements
the continuous transform following Torrence & Compo (1998), band-pass filtering by scale, and wavelet
coherence.

## First, where the variance actually is

Share of a name's return variance by horizon, on 2,000 sessions:

| | 2-5d | 1w-1m | 1m-3m | 3m-1y |
|---|---:|---:|---:|---:|
| RELIANCE | **63%** | 28% | 7% | 3% |
| HDFCBANK | **64%** | 28% | 6% | 2% |

Indian large-cap return variance is overwhelmingly short-horizon. That single table explains a great
deal of this project's results: a signal at a monthly horizon is trying to predict the **6-7%** of
variance that lives there, while paying costs charged against the whole of it.

It is also the variance ratio generalised. `vr_60 = var(5d) / (5 x var(1d))` compares exactly two
scales; this is the whole spectrum, and a name with structure at both ends is indistinguishable from
one with structure at neither under the ratio.

## Then, correlation by scale on a real book

Thirty liquid names, equal weight, 1,449 aligned sessions, effective bets recomputed from returns
band-pass filtered to each horizon:

| scale | avg correlation | effective bets | PC1 share | filtered vol |
|---|---:|---:|---:|---:|
| **daily (what the risk engine uses)** | **+0.284** | **7.13** | 33.6% | 16.2% |
| 2-5d | +0.289 | 6.86 | 34.5% | 11.6% |
| 1w-1m | +0.281 | 7.15 | 33.1% | 7.7% |
| 1m-3m | +0.225 | **8.61** | 26.8% | 2.9% |
| 3m-1y | +0.296 | **5.09** | 38.9% | 2.2% |

Effective bets range from **5.09 to 8.61** against the 7.13 the engine reports - a spread of ±25%.
At annual scale the book is **29% less diversified** than the daily correlation says.

## And the negative result, which is the honest headline

**It matters less than it should, because the long scales carry almost no variance.**

Read the last column. The 3m-1y band, where correlation is highest and diversification worst, has a
filtered volatility of 2.2% against the daily 16.2% - about **2% of total variance**. Being more
correlated in a band that carries a fortieth of the risk changes total portfolio risk by very little.

So the single daily correlation is a **defensible summary for total portfolio risk in this market**,
and the wavelet decomposition's contribution is to have established that rather than to have
overturned it. I expected to find the risk engine understating concentration at the holding horizon.
It does, at annual scale, in a band too small to matter.

The effect is not monotone either, which is worth stating because the first measurement suggested it
was. Three pairs of held names showed correlation rising steadily with scale - 0.21 to 0.39 - and on
thirty names it dips at 1m-3m and rises at 3m-1y. Three pairs was not a sample.

## Where it does matter

**A strategy whose returns live at long scales.** `mom_12_1` at h=40 rebalances monthly and holds
through the 1m-3m band, where this book is *more* diversified, not less. So the risk numbers for
[[h20]] are conservative rather than optimistic - which is the direction to be wrong in, and now
measured rather than hoped.

**Coherence, if a pair ever looks worth trading together.** `coherence()` returns amplitude and
phase, so it says which of two names leads and by how many sessions at each scale. Nothing here has
earned that question yet.

## Two documented biases walked into on the way

**The power spectrum is biased toward long scales.** Raw `|W|^2` is not comparable across scales -
the Morlet normalisation grows as `sqrt(s)` - so two equal-amplitude sinusoids at periods 12 and 96
returned peaks at 93.5 and 111.2, the second an adjacent scale of the *same* 96 cycle. The
12-session cycle was there the whole time and the statistic could not see it. Rectified per Liu,
Liang & Weisberg (2007) by dividing power by scale: the peaks become 12 and 94.

**Coherence is 1 everywhere unless smoothed properly.** Smoothed in time alone, two independent
normal series returned a mean coherence of **0.875**, which reads as "these move together". Torrence
& Webster (1999) smooth in scale as well. With both, independent series give 0.456 against a measured
null of 0.418 - and the null is now returned with every result, because 0.9 against a null of 0.87 is
noise while 0.9 against 0.42 is a finding, and the number alone cannot tell you which.

The cone of influence is honoured throughout. At a 250-session scale on an eleven-year series that is
a year and a half masked at each end, and not masking it would have produced a great deal of
plausible output computed from zero padding.

Related: [[h20]] · [[Per-stock selection loses to not selecting]] ·
[[The horizon was the binding constraint]] · [[Dynamic rules, and the correction to h12]]
