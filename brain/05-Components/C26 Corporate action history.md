---
type: component
phase: 0
status: done
resources: []
depends-on:
  - "[[C01 Security master]]"
tags: [component, phase/0]
---

# C26 Corporate action history

**Phase 0** · Splits, bonuses and price adjustments, derived from Tier-1 data

> [!success] Delivered — but derived, not fed
> `src/financial_brain/corpactions/detect.py`

NSE's corporate-action API sits behind `www.nseindia.com`, which rejects non-browser
clients (HTTP 403). Rather than build a fragile scraper, actions are **derived from data
we already hold**: on an ex-date the exchange restates `PrvsClsgPric` in the bhavcopy, so

```
factor = PrvsClsgPric(today) / ClsPric(previous session)
```

*is* the adjustment factor, published by the exchange itself.

## Corroboration
An action affects the security, so both exchanges must restate by the same factor; noise
will not agree. Candidates are graded `corroborated` (both exchanges) or
`single_exchange`.

## Two traps found in real data
- **Series contamination** — TCS carried a block-deal (`BL`) row with `prev_close`
  3019.00 against a clean `EQ` 2059.60, inventing a 1.47x action. Comparisons match on
  series.
- **Invented ratios** — a generic closest-fraction search called 0.8383 a "16:19 split"
  at 0.45% error. Only round ratios Indian actions actually use are accepted; everything
  else becomes `ADJUSTMENT` with the observed factor, which still adjusts prices
  correctly.

## Honest limit
Detects *that* an adjustment happened and by how much. Cannot distinguish a 1:1 bonus
from a 1:2 split, cannot see actions that do not move the reference price, and needs the
prior session in the lake. A strong Tier-1 substitute for a feed, not a replacement.

Related: [[C01 Security master]] · [[C19 India Implementability Gate]]
