---
type: research
tags:
  - research
  - validation
  - sample-bias
  - method
date: 2026-10-01
---

# The replay sample is not the problem

`evaluation/replay.py` says in its own comment that it is not a strategy backtest: a committee run
costs a minute of local inference, so it looks at the names each session *surfaced* rather than the
names available. Every result the replay has produced is therefore measured on a sample the replay
chose, and the question nobody had answered is whether that matters:

> did the system do well because it selected good opportunities, or because it was only ever shown a
> particular kind of opportunity?

The worry was that the sample was *easier* than the market. It is not. If anything it is slightly
harder - and at the setting the replay actually runs, the measurement cannot tell.

## The selector is three filters, and they were measured separately

    materiality = 'high'
    event_type IN (7 categories)
    ORDER BY filings DESC LIMIT per_day       <- the busiest names that session

The third is the one that gets missed: ordering by filing count and taking the top two selects names
having an unusually eventful day, which is a different population again and is the layer least likely
to have been intended as a selection.

Cumulative layers against the full eligible universe, 2023-01 to 2026-06, every fifth session:

    h20                                names/sess   share  selected    rest     diff       t  sess
    any filing                              323.3  22.43%    +1.55%  +1.71%   -0.16%   -2.18   173
    + materiality high                       51.0   3.54%    +1.28%  +1.69%   -0.41%   -2.41   173
    + one of 7 event types                   43.4   3.01%    +1.48%  +1.68%   -0.21%   -1.18   173
    + busiest 2 that session                  2.0   0.14%    +1.35%  +1.67%   -0.32%   -0.60   173

    h60
    any filing                              322.2  22.45%    +5.22%  +5.46%   -0.24%   -1.84   173
    + materiality high                       50.7   3.53%    +4.98%  +5.43%   -0.45%   -1.64   173
    + one of 7 event types                   43.3   3.01%    +5.36%  +5.42%   -0.06%   -0.18   173
    + busiest 2 that session                  2.0   0.14%    +4.97%  +5.41%   -0.44%   -0.41   173

**Every layer's point estimate is negative.** Names with filings do slightly *worse* than the eligible
universe over the next 20 and 60 sessions, which agrees with
[[The event archive is real and it is not tradeable]] - every event effect that cleared the Bonferroni
bar there was negative too. The replay has been working on a marginally harder sample than the market,
not an easier one.

## But the verdict is "no detectable bias", which is not the same as "no bias"

The broad layers *are* measurably negative: any filing at t=-2.18, high materiality at t=-2.41. Those
populations are 323 and 51 names a session, so there is enough cross-section to resolve a small
difference.

By the time the selector cuts to **two names a session** the t collapses to -0.60 and -0.41 while the
point estimate stays at -0.32% and -0.44%. The effect did not go away; the sample got too small to see
it. Two names across 173 sessions is 346 observations of a quantity whose cross-sectional standard
deviation is several percent.

So the honest reading is **absence of evidence, not evidence of absence**. If the -0.32% at h20 is
real, the replay carries a persistent headwind of about a third of a percent per decision, and this
measurement cannot rule it in or out. The fix is to raise `per_day`, which costs exactly the inference
time the low setting was chosen to save - the compromise is now quantified rather than assumed.

## What this does and does not settle

**It settles the direction.** The concern that drove this measurement - that replay results were
flattered by a cherry-picked universe - is not supported. The selection is not favourable.

**It does not validate the replay.** A sample being representative says results measured on it
generalise; it says nothing about whether those results were any good. The committee's own track record
is a separate and still-unanswered question.

**It is a shorter window than everything else here.** 2023-2026, not 2015-2026, and every fifth
session. The eleven-year version did not finish: the layer query joins 3.08M announcement rows against
a 900,000-row pool, and h60 alone took 1,057 seconds on the shorter window. The session count the t is
computed over - 173 - is far past what the statistic needs, so striding costs nothing that matters;
the window length costs coverage of 2015-2022, which includes the only sustained drawdown in the data.

## Two bugs found building the measurement

**`DISTINCT` applies after a window function.** Numbering price rows and de-duplicating afterwards left
`_sessions` holding sixteen copies of every date - 80 names a session, a fifth surviving the
modulo - which fanned the pool out sixteen times. Every count was wrong by that factor while the
per-session *averages* still looked entirely plausible, which is how it survived a first reading. The
dates are now made distinct before they are numbered, and a test asserts the pool holds one row per
(session, lineage).

**A 120-second fixture that was not slow.** Profiling blamed fixture construction for two minutes of a
five-minute test file; each statement in isolation ran in 0.01 to 0.14 seconds. The time was entirely
CPU contention from a real-data job I had left running in the background. The lesson is about the
measurement, not the code: a profile taken while something else is saturating the machine attributes
that machine's contention to whatever happens to be under the profiler.

Related: [[The event archive is real and it is not tradeable]] ·
[[What the book declined, and what it cost]] ·
[[Naive momentum beats it, and the horizon was a fit]] · [[Beating the median is not an edge]]
