---
type: research
tags:
  - research
  - pre-registration
  - tax
date: 2026-10-04
status: pre-registered
---

# PRE-REGISTERED tax-aware returns

Written and committed before anything is computed. Question: after Indian tax, does any active strategy
here beat simply buying the Nifty 500 and holding it?

## Rules used (configurable in `costs/tax.py`; checked against public sources on 2026-10-04, not tax advice)

* Short-term capital gains (equity held 12 months or less): **20%**, plus 4% cess = 20.8%. No exemption.
* Long-term capital gains (held over 12 months): **12.5%** plus cess = 13%, on gains above **Rs 1.25 lakh a
  financial year**.
* Intraday (speculative) profit: business income at the trader's slab rate (30% marginal assumed), losses
  set off only against other speculative profit.
* Short-term capital losses carry forward 8 years against later short-term (and long-term) gains.
* The depository charge (about Rs 15.34 a stock sold on delivery) is a cost, now in the cost model.

## Design

Rs 10 lakh starting capital, 129 twenty-session periods from 2016-04. Each sleeve's per-period **absolute**
return net of trading costs (the sleeve's return, not its excess). **Active** strategies are assumed to
realise every gain each period, so all gains are short-term and tax is paid at each 31 March on the year's
net realised gain (with loss carry-forward). **Passive** buy-and-hold of the Nifty 500 price index realises
nothing until the end, then pays long-term tax once on the whole gain above the exemption. Same dates, same
capital. Slow sleeves that in fact hold some names over a year are treated as all short-term (conservative).

## Predictions

* **T1** every active sleeve loses at least 15% of its pre-tax CAGR to tax.
* **T2** Nifty 500 buy-and-hold beats every active sleeve on after-tax terminal wealth.
* **T3** the momentum sleeve's after-tax CAGR is within 1.5 points of the Nifty 500's.
* **T4** realising gains every period instead of deferring them costs any sleeve with a pre-tax CAGR above 10%
  at least 1 point of CAGR.
* **T5** after tax, no swing setup and no opening-range-breakout variant turns positive against buy-and-hold:
  tax only ever reduces a positive result, it cannot rescue a negative one.

Pass criteria as written; no sleeve, rate, assumption or period is changed after seeing a result.
