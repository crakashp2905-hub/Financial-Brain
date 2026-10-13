---
type: research
tags:
  - research
  - result
  - edges
date: 2026-10-13
---

# Result of edges batch three

Tests [[PRE-REGISTERED edges batch three]] under [[ADR-0004 one evidence standard]]. Trials 3-5 of 2026Q4 (5 of 8
used). Code `evaluation/batch_three.py`; events `data/edges/{c1,n2f,eap}_events.parquet`.

| | first half | 2022-2026 | G3 | G4 (95th pct of null) |
|---|---|---|---|---|
| **C1** rich-basis carry, excess over risk-free per contract (608 contracts, 80 months) | +0.12%, t +1.44 (2016-21) | +0.12%, t **+3.89** | FAIL | **pass** (+1.44 vs -5.53) |
| **N2F** rejected results, futures short hedged with Nifty (688 events) | -0.62%, t -0.82 | -0.67%, t -1.45 | FAIL (wrong sign) | FAIL |
| **EAP** long into scheduled results, Nifty hedged (2,596 events) | -1.21%, t -1.81 | -0.40%, t -1.57 | FAIL (wrong sign) | FAIL |

## C1: positive in both halves, short of the bar in the first

The rich-basis carry earned the same +0.12% over the risk-free rate per contract in both halves; the 2016-21 half has
fewer rich months (47) and a wider spread. Against random contracts from the same months - the unconditional carry,
which loses to the risk-free rate - it separates by seven t-units. It is a yield, not a bet: per rupee of spot it is
roughly 6.5% + 1.4% a year, with the worst contract at 10%+ losing 2.0%. Already in the zoo as `carry_basis_10`
(since 2026-10-07); now monitored by `fb oos` against its 2022-26 band.

**C1a right, C1b wrong** (failed G3 on the first half).

## N2F: the effect does not survive the move to large caps and a Nifty hedge

N2 measured rejected-results names against the equal-weighted 500 and replicated on ranks 501-1000. Restricted to
F&O names and hedged with the Nifty, the short **loses**. Two reasons, both visible in the null: random F&O names
shorted against the Nifty lose 1.7 t-units on average (the F&O cross-section beat the Nifty over 2019-26), and the
flagged names are barely different from random ones. N2 stays what it was - an avoid filter in the 500, not a
tradeable short in the 200 largest. **N2Fa and N2Fb wrong.**

## EAP: the premium runs the other way here

Indian F&O stocks **lagged** the Nifty between the intimation of a results meeting and the session after it: -1.21%
per event in 2016-21, -0.40% in 2022-26, below random F&O names over the same windows (null mean -0.78). The
published premium (US and 32-country samples) is not present; if anything it is reversed - plausibly pre-results
de-risking by holders who know the date. **EAPa wrong**, EAPb right.

The reversal was found in-sample, so it is **not** a result: it is registered out-of-sample only in
[[PRE-REGISTERED out-of-sample batch three]] and paper-traded from the next session.

## What batch three changes

* Nothing reaches CANDIDATE. Two sleeves are positive net in both halves and short of t = 2 in one: M1 (momentum,
  t 1.45 / 1.24) and C1 (carry, t 1.44 / 3.89). Both are forward-tested.
* Every hedged-futures idea here pays a Nifty-hedge drag when the broad F&O universe outruns the index; a future
  hedged test should hedge with the equal-weighted F&O basket it trades against, which is a new registration.

Information, not advice.
