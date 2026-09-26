---
type: research
tags:
  - research
  - firewall
  - method
date: 2026-09-27
---

# The tie-break that cost three hypotheses

`above_ma200` and `above_ma50` are **BOOLEAN**. The benchmark casts them to double, so `x` is
0 or 1 and roughly half the universe ties on every rebalance date. `NTILE(5) OVER (... ORDER
BY x)` then split those ties by whatever order DuckDB's parallel scan happened to produce.

Three consecutive identical calls:

    above_ma200 top_excess    0.002587    0.002594    0.002669
    dist_52w_high             0.0070812107 (identical every time)

It surfaced because the same feature came back with an alpha t of **-1.56, -2.10 and -1.03**
in three runs an hour apart, which is not a difference any code change explains.

## The first consequence: nothing about those two features was a measurement

Every quintile number ever reported for them was one draw from a distribution. The rank IC was
never affected - `RANK()` gives tied rows the same rank, which is exactly why `ic_t` sat at
+3.69 across every run while the quintile wandered - so the significance result stood while
everything downstream of quintile membership did not.

## The second consequence, which is worse: the turnover was mostly phantom

Adding `lineage` to the ordering makes the split arbitrary-but-fixed rather than
arbitrary-and-varying. The same code path, before and after:

| | turnover | raw excess | alpha | alpha t |
|---|---:|---:|---:|---:|
| arbitrary tie-break, h=20 | **71%** | −0.316% | −0.231% | −2.10 |
| stable tie-break, h=20 | **30%** | −0.028% | +0.076% | +0.71 |
| stable tie-break, h=40 | 39% | +0.099% | +0.303% | +1.70 |

**Turnover fell from 71% to 30%.** When half the universe ties, an arbitrary split reshuffles
quintile membership every rebalance, so the book "traded" names it had no reason to trade and
was charged for every one of them. That churn was a property of the query planner.

And 70% turnover is the number **[[h11]]**, **[[h12]]** and **[[h13]]** were spent on. The
narrative across those three pre-registered trials was that `above_ma200` clears significance
at t = +3.69 and dies on turnover: h11 restricted the universe, h12 put a 5% hysteresis band
on it, h13 scaled the band to each name's own ATR. Three trials chasing a number that was
inflated by ties being reshuffled.

No verdict reverses - +1.70 against a bar of 3.59 is not close - but the reason three trials
were spent was partly an artifact of the measuring instrument.

## What the right treatment actually is

Not the alphabetical tie-break either. **A binary feature should not be quintiled at all.**
Holding "a fifth of the names above their 200-day average" is not a strategy anyone would run;
you would hold all of them, which is precisely what `evaluation/timeseries.py` does. The
quintile path was never the right instrument for these two features, and a stable tie-break
only makes the number reproducible rather than meaningful.

The lesson generalises past this case. The firewall gates a signal on quintile spread and
quintile turnover, and those quantities assume the signal is **continuous and
well-distributed**. Nothing checked that assumption, and two of the features in `FEATURES`
violate it completely.

## Why no existing control caught it

The synthetic null runs the same query on noise, so it inherits the same arbitrary split and
reports the same phantom turnover - a null that shares a bug cannot expose it. The
turnover-matched comparison in [[Running the strategies on noise]] would have matched real
phantom turnover against null phantom turnover and found nothing wrong.

What caught it was running the same measurement twice and getting different answers. That is
not a control this project had, and it is now one test: a planted feature that ties 100 names
against 100, asserted to give identical results over four consecutive evaluations.

Related: [[The toll was measured with one number and it needed thousands]] ·
[[Asking the question the factor actually claims]] · [[h11]] · [[h12]] · [[h13]] ·
[[Running the strategies on noise]]
