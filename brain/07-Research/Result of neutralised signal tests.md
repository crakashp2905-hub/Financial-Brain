---
type: research
tags:
  - research
  - result
  - neutralisation
date: 2026-10-06
status: result
---

# Result of neutralised signal tests

Pre-registration: [[PRE-REGISTERED neutralised signal tests]]. 120 sessions (every 20th, 2017-01 to 2026-08), the
500 most liquid names on each, 57,785 rows; home sector found for 99% of names. Residual correlation of the
signal and the forward return after both are regressed on the controls; one value per session. Bar 3.79.

## Forward 20 sessions (non-overlapping)

| signal | raw IC (t) | beta | + sector | + size | + volatility | kept after beta+sector+size |
|---|---|---|---|---|---|---|
| momentum 12-1 | +0.061 (4.4) | +0.060 (4.6) | +0.058 (4.7) | **+0.057 (4.7)** | +0.057 (4.9) | 94% |
| 52-week high | +0.059 (3.7) | +0.055 (4.2) | +0.053 (4.2) | **+0.053 (4.3)** | +0.048 (4.0) | 91% |
| composite | +0.046 (2.9) | +0.044 (3.5) | +0.048 (4.1) | **+0.047 (4.3)** | +0.038 (3.7) | 102% |
| low volatility | +0.017 (1.0) | +0.014 (1.2) | +0.021 (2.1) | +0.021 (2.2) | - | 129% |
| 20-day reversal | -0.017 (-1.6) | -0.020 (-2.0) | -0.017 (-1.9) | -0.018 (-2.0) | -0.021 (-2.4) | - |

At 60 sessions (overlapping windows, t overstated about 1.7x) the picture is the same: momentum keeps 96%, the
52-week high 87%, the composite 99%, low volatility 94% after beta + sector + size.

* **N1** momentum keeps at least half: **PASS** (94%).
* **N2** low volatility loses more than half once beta and size are removed: **FAIL.** It *gains* (raw t 1.0 to
  t 2.2): what hides it is sector - low-volatility names cluster in defensive sectors that lagged, and inside a
  sector the low-volatility names do better. Still not significant at 20 sessions.
* **N3** 52-week high keeps at least half: **PASS** (91%).
* **N4** composite keeps at least half and clears the bar: **PASS** (102%, t 4.25 after beta + sector + size;
  adding volatility as a control takes it to t 3.72, just under).
* **N5** reversal keeps at least half: **not applicable** - at 20 sessions on these monthly sessions the reversal
  signal has the wrong sign (continuation, t -1.6 raw). Its effect is shorter (half-life 16 sessions, strongest at
  1-5).

## What it means

The signals the brain ranks on are **stock selection, not disguised factor bets.** Removing market beta, the home
sector and size leaves momentum and the 52-week-high effect almost intact and makes the composite *cleaner* (its t
rises from 2.9 to 4.3, because sector and beta noise is removed from both sides). This also answers part of the
paper books' puzzle: the composite's ranking does carry information about relative returns; what the books lacked
was enough of it, after costs, at the horizons and position sizes they traded (and book-v1 tie-broke into a size
bet, which this test would have flagged).
