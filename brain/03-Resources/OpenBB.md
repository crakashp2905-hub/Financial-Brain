---
type: resource
verdict: WRAP
category: data
license: "AGPL-3.0"
license-risk: red
stars: 73000
url: https://github.com/OpenBB-finance/OpenBB
role: "Data abstraction — behind our own provider interface"
tags:
  - resource
  - verdict/wrap
  - category/data
  - licence/red
---

# OpenBB

**Data abstraction — behind our own provider interface**

> [!warning] Licence correction
> AGPL-3.0, not permissive. It had been designated the data core everything sits on.
> If Financial-Brain ships as a service, AGPL §13 reaches it. **INTEGRATE -> WRAP.**

Correct *shape* for the data plane — wrong licence to sit at the core. Treat as one
provider implementation behind `financial_brain.data.Provider`.

It is a data **abstraction**, never the Indian data solution: [[NSE]], [[BSE]],
[[Kite Connect]], filings, [[RBI]], news providers all still have to be built.
See [[C02 Data provider abstraction]].

---
Catalogued in [[MOC Resources]] · policy in [[Resource discipline]]
