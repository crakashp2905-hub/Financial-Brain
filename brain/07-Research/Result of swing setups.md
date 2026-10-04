---
type: research
tags:
  - research
  - swing
  - benchmark-error
date: 2026-10-04
status: result
---

# Result of swing setups

Predictions committed at `d018e64`. 2016-2026, liquid universe (turnover >= Rs 10 cr/day), entry at the close
of t+1, exit at the close after h = 5 or 10 sessions, one observation per signal date, sized round-trip costs
of about 0.4%. 74k to 196k trades per setup.

## The pre-registered outcome, and why it is misleading

Pre-registered outcome: return minus the **median** of the liquid universe over the same window.

    setup                       h   trades   gross    t    net     t    stopped
    SW1 pullback in uptrend     5   74,480  +0.69%  9.2  +0.24%  0.9     0%
    SW2 oversold in uptrend     5   47,821  +0.46%  8.6  +0.05%  0.2     1%
    SW3 breakout                5   97,653  +0.65%  9.8  +0.28%  1.6     1%
    SW4 strength continuation   5  196,062  +0.61% 12.7  +0.22% -0.1     0%
    SW1 pullback in uptrend    10   74,204  +0.94%  9.7  +0.49%  3.9     0%   CLEARS
    SW2 oversold in uptrend    10   47,703  +0.56%  7.3  +0.15%  1.4     1%
    SW3 breakout               10   96,998  +1.06% 12.2  +0.69%  6.2     0%   CLEARS
    SW4 strength continuation  10  195,051  +0.98% 17.2  +0.59%  8.0     0%   CLEARS

By the letter of the registration, three setups clear the bar net of costs, which contradicts prediction W3.
**That result is an artifact of my choice of benchmark, and I am reporting it as registered.** Stock returns
are right-skewed, so the *mean* of a liquid universe sits above its *median* (by +0.5% over 5 sessions and
+0.7% to +0.9% over 10: the "skew gap"). Subtracting the median therefore credits every long position with a
skew premium that any random name earns. The earlier event-study baseline (+0.9% after every kind of filing)
was the same effect, and I had already seen it.

## The corrected comparison: against the mean of the universe

An edge is what a setup earns beyond a randomly chosen liquid name, i.e. beyond the universe **mean**:

    setup                       h   skew gap  gross vs mean   t     net vs mean    t
    SW1 pullback in uptrend     5   +0.50%     +0.19%       1.4      -0.26%     -7.1
    SW2 oversold in uptrend     5   +0.47%     -0.01%       0.1      -0.42%     -8.4
    SW3 breakout                5   +0.56%     +0.09%       0.2      -0.28%     -7.8
    SW4 strength continuation   5   +0.54%     +0.07%      -1.2      -0.32%    -14.3
    SW1 pullback in uptrend    10   +0.76%     +0.17%       1.4      -0.27%     -4.6
    SW2 oversold in uptrend    10   +0.71%     -0.15%      -1.9      -0.56%     -7.8
    SW3 breakout               10   +0.86%     +0.21%       1.5      -0.17%     -4.3
    SW4 strength continuation  10   +0.83%     +0.16%       1.8      -0.24%     -7.6

The **gross** edge of every setup over a random name is between -0.15% and +0.21% and none of it is
distinguishable from zero (|t| <= 1.9). Net of a 0.4% round trip, **every setup is reliably worse than a random
liquid name** (t between -4 and -14). The "CLEARS" flags in this table are significant *losses*.

## Scorecard (against the registration, as written)

    W1 reversal setups: positive gross, net <= 0 at h=5     MIXED  gross vs median positive; vs the mean ~0; net vs median > 0 only because of the benchmark
    W2 breakout ~0 gross, SW4 slightly positive             PASS   on the corrected benchmark (+0.09%, +0.07% at h=5)
    W3 no setup clears the bar net                          FAIL on the registered benchmark (three clear); PASS on the corrected one (none is positive)
    W4 the ATR stop does not improve the net mean           PASS   but see below: the stop almost never fired
    W5 10-30% of trades are stopped                         FAIL   0-1%: 2 x ATR% x sqrt(h) is a very wide stop on these names

**The stop test was uninformative.** The registered stop distance (2 x ATR% x sqrt(h)) is about 9-14% on
these names, so it fired on 0-1% of trades and could not change the result. A tighter stop would have to be
a new pre-registered test, and the exit-rule study already found stops neither help nor hurt on average.

## What this means for a swing trader

Buying a classic pullback, oversold bounce, breakout or strength continuation in a liquid Indian stock and
holding it a week or two earns roughly what any liquid stock earns, *before* paying about 0.4% to enter and exit.
After costs the setup loses to a random pick by 0.2% to 0.6% a trade. The setups are not harmful by
themselves; the round trip is what makes them lose. Holding fewer, longer positions (the momentum/position
style) pays that cost less often.

## The lesson I am recording against myself

I registered a median benchmark after already having seen, in the event study, that the mean-median gap is
large. The registration should have used the mean. A benchmark that hands every long position a free premium
turns noise into a "finding", which is exactly the failure this project exists to prevent, and the only reason
it was caught is that three setups cleared at t of 4 to 8 when my prediction was zero.

Related: [[PRE-REGISTERED swing and intraday setups with stops]] · [[Why costs are bigger than the edge]]
