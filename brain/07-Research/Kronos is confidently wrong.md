---
type: research
tags:
  - research
  - forecasting
  - foundation-models
  - method
date: 2026-09-28
---

# Kronos is confidently wrong

Kronos (Shi et al., AAAI 2026) is a decoder-only foundation model over K-lines, trained on 12B+ bars
from 45+ exchanges, and its paper reports a 93% RankIC improvement over the leading time-series
foundation model on its own benchmark. The question worth asking was never "does it predict stocks".
It was:

> does it produce information about Indian securities that survives being measured point-in-time,
> compared against a bootstrap of the name's own history, and charged for the 150 trials already spent?

## The bar was set before the model was attached

That ordering matters. Three nulls, walked over the same grid the candidate would later walk:

* a **random walk** at the name's own recent volatility - which under weak-form efficiency is not a
  null to be beaten but the correct answer;
* a **drift** null, separating "this stock goes up" from "this stock goes up *now*";
* **climatology**, a stationary block bootstrap (Politis & Romano 1994) of the name's own returns,
  carrying its fat tails and volatility clustering and knowing nothing about the present. The margin
  over this one is the part of a model's skill that is conditional.

## The study

kronos-mini, 256 bars of context, h20, stride 20 so no two observations share a future return, 10
names a session chosen point-in-time by turnover, 2023-01 to 2026-06. **434 forecasts, 67 lineages, 44
sessions, 20 paths each.** 89 minutes of CPU inference. Every null ran on exactly the same 434 pairs.

    model            CRPS   sharp90   cov90   cov50   PIT chi2   edge mass   shape
    kronos-mini   113.127    236.41   47.0%   23.0%      358.4       53.7%   too narrow
    climatology    83.999    393.68   81.8%   47.2%       38.7       19.4%   no strong shape
    drift          84.426    400.19   81.3%   48.4%       39.8       19.4%   no strong shape
    random-walk    78.635    403.66   83.2%   47.2%       23.6       17.7%   no strong shape

    vs random-walk   skill -0.4386    LOSES TO it, while being sharper
    vs drift         skill -0.3400    LOSES TO it, while being sharper
    vs climatology   skill -0.3468    LOSES TO it, while being sharper

**It loses 44% of CRPS to a drift-free random walk.** And it is *sharper* than every null - intervals
236 wide against their ~400 - which is the whole problem: it is confidently wrong. Its nominal 90%
interval contains the outcome **47%** of the time, and its nominal 50% interval **23%**. PIT chi-square
358.4 on 9 degrees of freedom, with 53.7% of observations in the two edge bins against 20% expected.
The intervals are roughly **half** the width they need to be.

### The artifact that would have explained it away, and does not

Twenty sampled paths underestimate a distribution's spread, so a thin ensemble reads as too narrow for
reasons that have nothing to do with the model. The nulls settle it: **at the same 20 paths they get
81-83% coverage at nominal 90%.** The thin ensemble costs about seven points of coverage. Kronos is
missing forty-three. That is the model.

Two other places this study would have flattered itself, both caught before it ran:

**`predict(sample_count=30)` does not return 30 paths.** Kronos' `auto_regressive_inference` runs the
samples in parallel and finishes with `np.mean(preds, axis=1)`, so it returns one *mean* path however
many were asked for. Passing that off as a distribution gives zero spread, zero-width intervals and a
CRPS that scores as a **flawless** forecast. Real draws come from `predict_batch` with the window
repeated and `sample_count=1`.

**The naive ensemble CRPS penalises the smaller ensemble** by (n-1)/n, so a 20-path model measured
against a 1000-path bootstrap loses on arithmetic. Measured at 20 members: fair 0.2713, naive 0.3015 -
an 11% handicap. Everything above uses the fair estimator (Ferro 2014).

## What it does have

Two results point the other way, and they are the reason this is not simply a rejection:

    direction     hit rate   base rate    edge
    kronos-mini      56.2%       51.4%   +4.8%
    climatology      51.8%       51.4%   +0.5%
    drift            50.2%       51.4%   -1.2%
    random-walk      44.7%       51.4%   -6.7%

    cross-sectional IC   mean +0.1018   t +1.90   over 44 sessions

A **+4.8 point** directional edge over the base rate, where no null has one, and a cross-sectional IC
of **+0.10**. Quoted against the base rate deliberately: 56.2% is not a 56-point edge, and an IC of
0.10 is respectable for a signal nobody fitted to this market.

So the split verdict is: **the median carries information and the uncertainty around it does not.**
That is a coherent and useful thing to know. It says the distribution cannot be used for position
sizing or stop placement as it stands, and it says the ranking might be worth something.

## Which the ledger then charges for

The study is trial **151** in `evaluation_runs`, as `forecast:kronos-mini`. Recording it moved the
Bonferroni bar from **3.590 to 3.591** for every other signal in the project, because a
102M-parameter model is not exempt from arithmetic a moving-average crossover has to satisfy.

    IC t +1.90 against a trial bar of 3.59     REJECT
    family bar 2.73 on the search-penalised statistic +0.24 after 4 variants     REJECT

An IC t of 1.90 is not significant on its own terms, let alone against the bar. **Verdict: REJECT**,
on the stated ground that it does not beat any null.

## What would change the answer

**Temperature and top_p were 1.0 and 0.9 and were never calibrated.** A model whose intervals are
half the right width is the signature of sampling that is too concentrated, and temperature is the knob
for exactly that. That is a genuine, cheap experiment - and it is also a search, so it goes in the
ledger too.

**Twenty paths is thin for the tails**, which is why `quantiles()` refuses to report p05 below 40. The
CRPS comparison is fair at 20, but a serious calibration measurement wants more, and inference is 20
seconds a forecast on CPU.

**44 sessions is a small sample for an IC**, and 434 forecasts across 67 names on 44 sessions carry
fewer independent observations than the count suggests - names in a session share the market factor.

**Fine-tuning on Indian data is the obvious next move and it is the wrong one next.** A base model
that loses 44% of CRPS to a random walk and is miscalibrated by a factor of two is not short of Indian
data; it is mis-specified for the task as configured. Calibrate the sampling first, on the model that
exists. The dataset exporter with purged chronological splits is built and waiting either way.

Related: [[Stock movement prediction has no edge]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]] ·
[[The engine was the strategy's biggest short position]] · [[Running the strategies on noise]] ·
[[Beating the median is not an edge]]
