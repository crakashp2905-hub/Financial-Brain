---
type: research
tags:
  - research
  - result
  - technical
  - candles
  - fno
date: 2026-10-14
---

# Result of chart and candle setups in futures

Tests [[PRE-REGISTERED chart and candle setups in futures]] under [[ADR-0004 one evidence standard]]. Trial 7 of
2026Q4, one trial for the family of 17. Code `evaluation/chart_setups.py`; events `data/edges/k7_events.parquet`
(128,027 events, 209 F&O names, 2016-2026).

## Verdict: FAIL - 0 of 17

Per event, side x (stock - equal-weighted F&O) over the close of t+1 to the close of t+6, before and after 20 bp:

| setup | side | events | gross 2016-21 / 2022-26 | net t 2016-21 / 2022-26 |
|---|---|---:|---|---|
| bullish marubozu | long | 1,749 | +0.03% / -0.13% | -0.89 / -2.55 |
| hammer | long | 1,363 | +0.30% / -0.04% | +0.39 / -1.47 |
| bullish engulfing | long | 9,061 | +0.03% / -0.01% | -2.54 / -3.99 |
| morning star | long | 7,343 | -0.16% / -0.04% | -4.22 / -4.24 |
| sweep of the lows | long | 8,437 | +0.04% / -0.02% | -1.50 / -3.30 |
| break of structure up | long | 12,645 | -0.06% / -0.07% | -4.28 / -5.20 |
| bullish FVG retest | long | 15,373 | +0.05% / +0.01% | -2.45 / -5.55 |
| gap up, unfilled | long | 4,163 | -0.14% / +0.03% | -2.43 / -1.82 |
| 55-session high on volume | long | 5,110 | -0.03% / -0.10% | -1.23 / -3.14 |
| bearish engulfing | short | 14,042 | -0.12% / -0.03% | -5.65 / -7.14 |
| shooting star | short | 1,219 | -0.02% / +0.07% | -0.88 / -1.09 |
| evening star | short | 5,388 | -0.22% / -0.00% | -3.98 / -3.41 |
| sweep of the highs | short | 11,486 | +0.03% / +0.05% | -2.37 / -2.10 |
| break of structure down | short | 11,613 | -0.10% / -0.01% | -4.57 / -3.36 |
| bearish FVG retest | short | 12,631 | -0.03% / -0.00% | -3.68 / -4.44 |
| gap down, unfilled | short | 3,640 | +0.06% / +0.17% | -0.54 / -0.23 |
| 55-session low on volume | short | 2,764 | -0.59% / -0.04% | -2.48 / -2.18 |

## Predictions

* **K1 wrong**: the four candle patterns did **not** keep their directions. Bullish marubozu, +0.95% at five sessions
  in the matched study, is +0.03% / -0.13% here; bearish engulfing, -0.62% there, has the wrong sign here.
* **K2 right** (at most two pass: none did). **K3 right** (the smart-money setups fail).

## Why the earlier candle result does not survive

Three things differ from the matched event study, and each is a reason to trust this one more for trading:

1. **Entry one session later.** A candle is defined by its own close; buying *that* close puts the bid-ask bounce in
   the return ([[Intraday candles and the bid-ask bounce]]). Entering at the next close removes it, and the effect
   goes with it.
2. **The F&O 200, not the 500.** The largest names are the most arbitraged.
3. **Hedged with the traded universe.** Gross effects of +/-0.1% are the size of noise; a 20 bp futures round trip
   is twice that.

Chart and candle setups on daily bars have now been tested as cross-sectional factors (322 features), as matched
events, and as tradeable futures events. None is an edge here. Every setup runs in the paper zoo
(`edge_k7_<setup>`) with this verdict beside it.

Information, not advice.
