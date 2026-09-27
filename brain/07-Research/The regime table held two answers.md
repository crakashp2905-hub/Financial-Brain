---
type: research
tags:
  - research
  - method
  - firewall
date: 2026-09-27
---

# The regime table held two answers and eleven readers took either

`market_regime` is append-only by design, and the design is right: `regime/brain.py` says a rule
change **adds a version** rather than rewriting history, so a decision made in 2024 can still be
replayed against the classifier that existed then.

The consequence nobody had followed through is that the table holds **every version at once**. It
has 2,894 sessions and 5,788 rows.

## Eleven readers, no version filter

Found by an API route reporting **5,788 sessions for a 2,903-session archive**. The pattern that
caused it appeared in four modules:

```python
regimes = dict(con.execute("SELECT business_date, regime FROM market_regime").fetchall())
```

Two rows per date collapse into one dict entry, and which one survives is whichever the scan
yielded last. Another four read a single row ordered by date alone, which picks a version
arbitrarily for that date.

## It matters because the versions disagree on a seventh of the archive

**425 of 2,894 sessions (14.7%).** v2 introduced the `NARROW` regime, and it reclassified
sessions v1 had called RISK_OFF or NEUTRAL:

| | RISK_ON | RISK_OFF | NEUTRAL | NARROW | CRISIS |
|---|---:|---:|---:|---:|---:|
| **v1** | 37% | 32% | 28% | — | 3% |
| **v2** | 43% | 24% | 22% | **8%** | 3% |

So four things were reading a non-deterministic label on 14.7% of sessions:

* the **firewall's regime gate**, which rejects a signal whose IC significantly reverses in any
  regime - a gate whose input varied between runs;
* the **time-series harness's** regime reporting;
* `evaluation/concentrated.py`;
* the **stress engine's** conditional volatility, added the same day, which is where the measured
  2.70x CRISIS multiplier came from.

And `decisions/safety.py`'s **crisis check**, which blocks new longs in a CRISIS regime, read a
single row ordered by date alone. That refusal was a coin flip on the disagreeing dates.

## The same defect as the tie-break, in a different table

[[The tie-break that cost three hypotheses]] found `NTILE` splitting a boolean feature's 50/50 tie
by scan order. This is the same shape: **an unspecified choice resolved by whatever the query
planner did**, producing a plausible number that changes between runs. Both were found by
noticing an arithmetic impossibility rather than by any control - a turnover of 70% that should
not have been, a session count of 5,788 that could not be.

Neither the synthetic null nor the trial ledger could have caught either. A null runs the same
query and inherits the same ambiguity.

## Fixed

`regime/brain.py` now owns the accessor: `latest(con)` reads the newest version present rather
than hard-coding `VERSION`, so an older database still answers, and `series(con)` returns
`business_date -> regime` for exactly one version. Every dict-building reader calls it. The four
single-row readers order by `version DESC` as well as by date. `worldstate/build.py` was already
correct and is the reason the pattern was recognisable.

`services/market` reports the version alongside the value, because "RISK_OFF" without a version
is ambiguous in this archive and a caller cannot tell that from the string.

## What has to be re-checked

Every result whose gates consulted a regime. The firewall's regime gate is a *refusal* gate - it
rejects on significant reversal - so a mixed label could only have caused a spurious rejection or
missed one, and nothing in the ledger passed.

The **2.70x CRISIS volatility multiplier** was re-measured on v2 alone and came back **identical**
(CRISIS 52.6%, RISK_ON 19.5%, NARROW -0.150% a day). DuckDB's scan order had been yielding v2
consistently over that window, so the figure was already the v2 figure.

That is worth stating precisely rather than claiming a correction that did not happen: the number
was **right by luck and is now right by construction**, and the difference is whether it stays
right when the query planner changes its mind. Nothing about the measurement moved; the reason to
trust it did.

Related: [[The tie-break that cost three hypotheses]] · [[Market Regime Brain]] ·
[[Alpha Validation Firewall]]
