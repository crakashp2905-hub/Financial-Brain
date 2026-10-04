---
type: research
tags:
  - research
  - intraday
  - execution
date: 2026-10-04
status: result
---

# Result of the intraday study and the slippage calibration

Predictions committed at `13e0de2`. Minute bars pulled from Kite (read-only endpoints): 300 liquid names
(was 100), 54.9M bars, 2024-09-25 to 2026-10-01, 498 sessions, 146,311 (name, session) rows.

## A leak, caught by a result that was too good

The first run gave `vwap_dev` an IC of **+0.238 at t = +49.7**. Nothing real predicts at t = 50. The
feature was measured at 11:00 while the outcome started at 10:30, so the outcome window contained the
feature's own last 30 minutes. My feature spec and the pre-registration both carried that inconsistency.
`vwap_dev` is now measured at 10:30, a regression test (`test_no_feature_sees_a_bar_after_the_outcome_starts`)
changes every bar after 10:30 and asserts no feature moves, and the study was re-run. The corrected
result is below; the leaky one is not used. This is a correction of a leak, not a rescue of a result.

## Corrected results (10 trials, bar 3.715)

    feature      outcome            IC      t     prediction      verdict
    gap          r_to_close       -0.0081  -2.04   fade, |t|<3.7   PASS
    or_breakout  r_to_close       -0.0102  -2.49   continue, +     sign WRONG (breakouts fade), null
    vwap_dev     r_to_close       +0.0070  +1.48   revert, -       sign WRONG, null
    rvol_30      r_to_close       -0.0055  -1.45   none            PASS
    range_1h     r_to_close       -0.0336  -5.85   no direction    FAIL: clears the bar
    range_1h     |r_to_close|     +0.2253 +58.80   size, t>10      PASS (a volatility fact)

* **Opening-range breakouts fade, they do not continue.** I predicted the popular rule would be
  arbitraged to nothing; it is, and slightly inverted, at t = -2.5 which is not significant.
* **`range_1h` has a real directional IC** (a wide first hour precedes a lower afternoon return) and I
  predicted none. **It is not tradeable**: the narrowest-minus-widest decile spread is **+0.5 bp (t 0.17)**
  against an intraday round trip of 14 bp (mega-cap) to 54 bp (mid-cap). A rank IC of -0.03 sits in the
  middle of the distribution, not the tails, and the tails are what a trade uses.
* **N6 holds: nothing is tradeable.** Predictions N1-N6 stand, with N2/N3 signs and N5 direction wrong.

## Execution slippage calibration

34,866 simulated buys across 300 names, walking the real minute tape at 10% participation from 10:31:

    order           realised shortfall     sqrt-impact model     fills where model >= realised
    Rs 5 lakh       +0.2 bp (p90 +12.8)    4.3 bp                64%
    Rs 25 lakh      +0.2 bp (p90 +23.3)    9.7 bp                67%
    Rs 25 lakh by liquidity:   ADV < 30 cr   -2.7 vs 34.2 bp   |   30-100 cr  -0.6 vs 13.9   |   > 100 cr  +0.7 vs 5.9

The cost model's impact term (Y = 1) is **well above** what the simulator pays. Two reasons not to read
that as the model being too pessimistic: the simulator fills at the minute's typical price, which is
optimistic for a buy (the spread is unmeasured by design, as its docstring says), and a three-minute-bar
arrival price of the 10:30 close carries no adverse selection. The correct reading is that **the
square-root term is not contradicted** and the real cost sits between the two, in the unmeasured spread.
Capacity is not the binding constraint: fill rate is 98-100% even for Rs 25 lakh in names under Rs 30 cr
of daily turnover.

Related: [[PRE-REGISTERED intraday features]] · [[Result of the opportunity engine replay]]
