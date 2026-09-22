---
type: strategy
tags:
  - strategy
  - tested
verdict: REJECT
---

# Momentum

**Claim.** Winners over 12 months, skipping the last, keep winning (Jegadeesh & Titman 1993).

**How it was built here.** `mom_12_1`, long the top quintile, monthly rebalance, Rs 1cr liquidity floor.

**Verdict.** REJECT - deflated Sharpe 0.00 after 19 counted trials. The IC itself was not the problem; surviving the trial count was.

**What would change the answer.** A longer history, or a universe where the effect is less crowded. Not re-running the same test with different parameters, which the trial counter would punish.

Related: [[MOC Strategies]] · [[Alpha Validation Firewall]] · [[Procedural memory]]
