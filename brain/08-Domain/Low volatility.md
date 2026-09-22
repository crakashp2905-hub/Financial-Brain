---
type: strategy
tags:
  - strategy
  - tested
verdict: REJECT
---

# Low volatility

**Claim.** Low realised volatility earns at least as much as high (Blitz & van Vliet 2007).

**How it was built here.** `vol_60` ranked ascending, top quintile long, monthly.

**Verdict.** REJECT - the top quintile loses **0.37% per period** after costs at 25% turnover.

**What would change the answer.** Lower turnover by rebalancing less often, or a size-neutral construction. The signal was not the failure; the round trip was.

Related: [[MOC Strategies]] · [[Alpha Validation Firewall]] · [[Procedural memory]]
