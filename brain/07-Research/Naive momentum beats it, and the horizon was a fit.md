---
type: research
tags:
  - research
  - validation
  - walk-forward
  - baselines
date: 2026-09-28
---

# Naive momentum beats it, and the horizon was a fit

Two experiments, both aimed at the only question that can stop this project: does the process have an
economic edge, or does it have a number that was chosen by looking? Both answers are negative, and
they are the most useful results this project has produced.

## The horizon does not survive being chosen honestly

h60 was selected by sweeping seven configurations over 2015-2026 and reading off the best, then
reported on 2015-2026. So: choose the horizon on a training window, spend it on the next one, roll
forward, with a 120-session purge gap so the last training rebalance's forward returns cannot land in
the test window.

    fold   window            h20              h60             h120
                          train    test    train    test    train    test
     0     1,200 / 400    +60.27  -18.27   +91.95  -17.45   +44.97   -7.87
     1     1,600 / 400    +60.39  -31.65  +125.64   -4.75   +52.55   -0.20
     2     2,000 / 400    +22.09   +0.24  +169.47   -0.26   +60.50  +10.12

    the selection chose h60 in every fold, and never changed its mind

    mean out-of-sample excess
      the selection (h60 each time)    -7.49%
      fixed h20                       -16.56%
      fixed h60                        -7.49%
      fixed h120                       +0.68%

    folds where the selection was positive out of sample:  0 of 3

**The training excess is anti-correlated with the out-of-sample excess.** h60 trains best in every
single fold (+91.95, +125.64, +169.47) and tests badly. h120 trains worst in every single fold
(+44.97, +52.55, +60.50) and is the only horizon with a positive out-of-sample mean. That is not a
weak result or a noisy one; it is the textbook signature of a parameter fitted to its training window,
and the fold that trains most spectacularly (+169.47%) tests at -0.26%.

So the **+246.55%** excess in
[[The engine was the strategy's biggest short position]] is in-sample, and the horizon that produced it
has *negative* out-of-sample value. Selecting the horizon was worse than picking h120 in advance and
never looking.

## And a floor comparison I got wrong

The same note claimed h60's excess sat 212 points clear of the engine's calibration floor. It does
not. The floor grows with the horizon - fewer rebalances means less trimming, so weights drift further
inside the no-trade band and the whole-universe book picks up more of a momentum tilt that the
cost-free control never gets:

    calibration floor at h20    +34.26%
    calibration floor at h60   +130.79%

I compared an h60 result against the h20 floor, which is comparing two different instruments. The real
margin is about **116 points**, not 212. `evaluation/economic.baselines` now computes the floor at
whatever rebalance it is judging, so the mistake is not available any more.

## Naive twelve-month momentum beats it, more than twice over

Same engine, same universe, same costs, same control, h60, top 100, eleven years:

    strategy          net return     EXCESS   gross excess   Sharpe   maxDD   costs
    mom_12_1            +914.17%   +585.40%      +759.21%    +1.13   -51.8%   82.2%
    dist_52w_high       +575.32%   +246.55%      +413.57%    +1.18   -39.3%   81.2%
    vol_60              +190.70%   -138.06%       -83.55%    +0.98   -32.9%   34.1%
    ret_20d              +17.58%   -311.18%      -266.58%    +0.18   -71.0%   34.2%

    equal-weight universe, held    +328.76%
    Nifty 500                      +241.61%
    Nifty 50                       +194.21%
    engine calibration floor       +130.79%

**Ranking on a twelve-month return beats the candidate by 339 points of excess.** Jegadeesh & Titman
published that in 1993. It needs no foundation model, no committee, no knowledge graph, no
point-in-time announcement archive and no promoter group - one SQL column and a sort.

The candidate's only advantage is risk-adjusted: Sharpe +1.18 against +1.13, and a -39.3% drawdown
against -51.8%. That is a real difference and it is not nothing, but it is a long way from what 150
trials of search was supposed to buy.

Two of the four baselines are strongly *negative* against the universe, which is worth stating because
it means the universe control is doing its job: short-horizon reversal (-311%) and low volatility
(-138%) would both look like winners against Nifty 500 on total return alone. `ret_20d` returned
+17.58% while the index returned +241.61%, and any comparison that stopped at "it made money" would
have missed it.

## What is not tested here

**Naive value, naive quality and sector-neutral momentum are absent, and are not faked.** `features`
holds 34 columns and every one derives from price and volume; there are no point-in-time fundamentals
and no sector labels in this archive. Building those baselines from present-day fundamentals applied to
2016 would make them easier to beat, which is the wrong direction to be wrong in.

**Only three horizons were walked forward.** A wider grid would be more trials charged against the
whole ledger, and a grid wide enough to guarantee a winner in every fold guarantees nothing about the
next one.

**Three folds is a small number of out-of-sample observations**, covering roughly five years. The
direction is consistent across all three, and the anti-correlation between train and test is what makes
it convincing rather than the sample size.

## What this changes

The honest state of the project is now: **no validated edge over a trivial baseline.** The
infrastructure is real, the measurements are real, and what they measure is that a 1993 momentum sort
does better than everything built on top of it.

That is worth more than the alternative, which was continuing to build on a result that had never been
chosen honestly. The next question is not "what else can be added" but the one the baselines pose:
**what does any of this do that `ORDER BY mom_12_1 DESC` does not?** Until something answers that out
of sample, complexity is a cost.

Related: [[The engine was the strategy's biggest short position]] ·
[[Where the money actually came from]] · [[Kronos is confidently wrong]] ·
[[A hundred and fifty trials are not a hundred and fifty discoveries]] ·
[[Beating the median is not an edge]] · [[One bad control from writing off momentum]]
