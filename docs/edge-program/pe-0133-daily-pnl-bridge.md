# PE-0133 — daily signed P&L bridge

`daily_loss_bridge()` aggregates trade-level signed INR effects into a per-day bridge: forecast gross, selection effect, spread, impact, charges, timing, and risk sizing. Each day is marked `RECONCILED` only when every component and realized net is present and their sum agrees within the declared tolerance. Expenses use negative signs. Missing components remain null with trade IDs, and an arithmetic residual is exposed whenever all needed values are available.

**Status: PARTIAL.** The standalone bridge and arithmetic checks pass 9 focused bridge tests; 6 existing loss-review tests also pass. The current intraday ledger records gross/net, charges, decision/fill prices, and some execution diagnostics, but does not persist a frozen forecast baseline or complete spread/impact, timing, and counterfactual risk-sizing effects. The bridge accepts caller-provided effects and verifies arithmetic only; it does not prove those decompositions are causal or connect them to each losing paper day. Until those inputs are captured and reconciled from the authoritative paper ledger, reports must remain incomplete.

Evidence and source hashes are recorded in `pe-0133-daily-pnl-bridge-evidence.json`.
