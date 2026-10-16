---
type: research
tags:
  - research
  - result
  - fundamentals
  - quality
  - value
date: 2026-10-15
status: failed
---

# Result of the multi-factor quality value low-risk book

Registered in [[PRE-REGISTERED multi-factor quality value low-risk book]] (2026-10-15, before any computation). Run
the same day by `evaluation/fundamental_tests.py` (`mf_signals`, `mf_book`). Recorded in the ledger as trial **MF**
(amendment 4).

## What ran

83 monthly rebalances from 2019-10 to 2026-08. On average 276 names were eligible (among the 500 most liquid, four
known quarters, positive TTM profit) and the book held 56 of them. Monthly one-way turnover was 23%. Fundamentals are
first-reported and used only from their filing time. Shares come from paid-up capital divided by face value in the
same filing, with the unadjusted month-end close.

## Result: FAIL (G3, G4)

| | before 2022 (27 months) | 2022 on (56 months) | full |
|---|---|---|---|
| net excess over EW eligible, per month | -0.44% (t -1.05) | -0.14% (t -0.55) | -0.23% (t -1.09) |

G4: the replication statistic of -1.05 sits **below** the random books' 95th percentile (-0.13; their mean is -1.45).

Each signal's own top fifth, net excess (reported, not gating):

| signal | t before 2022 | t 2022 on | full t |
|---|---|---|---|
| earnings yield (value) | +1.23 | +1.46 | **+1.90** |
| net margin (profitability) | -1.42 | +0.42 | -0.65 |
| earnings stability | -0.09 | -2.95 | -1.56 |
| low volatility | -1.53 | -1.57 | **-2.19** |

## Against NSE's indices, same 82 months

| | cumulative | annual vol |
|---|---|---|
| the book, net of costs | +248% | 19.9% |
| equal-weighted eligible universe (its benchmark) | +299% | 22.8% |
| NIFTY Alpha Quality Value Low-Volatility 30 | +170% | 15.5% |
| Nifty 500 | +142% | 18.4% |
| NIFTY500 Value 50 | +334% | 24.9% |

The book beat the Nifty 500 and the NSE blend index. That came from holding liquid mid-caps in a broad rally, not
from the signals: the same names equal-weighted did better still. Over 2019-26 in India, low risk and stable earnings
**cost** returns. Value was the only component that helped, and it fell short of the bar.

## Predictions

* **B1** (positive in both halves): **wrong**, negative in both.
* **B2** (fails G3 in the short first half): right, and it also fails in the second.
* **B3** (value contributes most, profitability least): value most, right. The least was **low volatility**, not
  profitability.

## What it means

A "Buffett-style" blend, as defined here, did not beat its universe in this market and period. The value leg alone
(full t 1.90) is the only part worth a future registration, as a new trial with its own hypothesis. It cannot be
promoted from this run, because choosing the best component after seeing the parts is the selection the standard
forbids.

Information, not advice.
