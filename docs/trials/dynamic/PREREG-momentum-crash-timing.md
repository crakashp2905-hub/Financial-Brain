# Preregistration: momentum crash timing on H005 (written 2026-11-18, not run)

**Status: REGISTERED, NOT RUN.** It runs once, when the 2027Q1 protected budget opens. If that
budget is never authorised, it stays unrun.

## Why

Momentum's known failure mode is the "momentum crash": after a long market fall, the losers rebound
hardest, and a momentum book that is long recent winners loses sharply. March to May 2020 is the
Indian example. The academic version of this is Daniel and Moskowitz (2016). A rule that cuts
exposure in that state is the one timing idea with a published mechanism behind it.

## Book

H005, industry momentum. It is the only momentum book that still passes once BE/T prices are filled
in (bet-recheck-2026-11-18.json). It is built exactly as registered in hundred.py, on the BE/T-filled
price panel from `scripts/research/bet_recheck.py`.

## Rule (fixed now, no parameters to tune)

At each monthly rebalance date d:

- **Bear state:** the Nifty 500 total-return index has a negative return over the trailing 24 months
  to d.
- **In the bear state**, hold 50% of the H005 book and 50% cash at the risk-free rate.
- **Otherwise**, hold 100% of the book.
- Switching costs are charged on the exposure that changes, using hundred.py's `timing()` cost.

There is no other condition, threshold or lookback. It is the textbook rule, with its own published
lookback and a single 50% scale.

## Test and pass bar

- **Statistic:** the timing alpha from `hundred.timing()`, comparing the rule against the same book
  held at its own trailing average exposure, so a rule that simply holds less is not rewarded.
- **Grading:** ADR-0007 `dynamic_standard.judge`, prior B (published mechanism, untested in India),
  family "timing". The family size is recomputed at run time; ADR-0007 is not modified.
- **PASS** needs a positive timing alpha in both halves and a tier of CANDIDATE_SMALL or better.
- **Secondary, reported but not graded:** worst month, worst 3 months and maximum drawdown, against
  the always-on H005 book.

## What will not be done

- No changing the 24-month lookback, the 50% scale or the index after seeing results.
- No applying it to P3 or H003 and keeping the best.
- A different rule is a new registration.
