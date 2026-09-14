---
type: concept
tags:
  - concept
  - india
  - blocker
---

# Indian microstructure

India is unusually punitive and it breaks naive backtests.

- **STT** on both legs of delivery, plus stamp duty, exchange charges, SEBI turnover fee,
  GST, brokerage. Round-trip friction is a large multiple of a US equivalent — it kills
  high-turnover factor strategies
- **Impact cost** outside the top ~500 names is severe
- **Circuit limits** (2/5/10/20%) mean you often cannot trade at the signal price — the
  backtest fills, reality does not
- **ASM / GSM surveillance**, T2T segment, periodic call auctions — names get frozen or
  forced to delivery-only with little notice
- **No shorting in cash beyond intraday.** SLB is thin. Long-short academic results —
  most of the literature — are **not implementable** except via F&O on ~200 names

Hence [[India Implementability Gate]] and [[C05 Indian cost model]].
