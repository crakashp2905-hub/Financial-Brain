# PE-0136 — matched opportunity and no-trade controls

`compare_matched_controls()` compares each treated opportunity’s net return with a contemporaneous control selected within frozen relative calipers for notional size, quoted-liquidity spread, and risk budget. Controls are not reused. The report preserves every treated opportunity, including unmatched rows and their reasons, and reports only descriptive paired net-return differences with explicit causal limitations.

**Status: PARTIAL.** Eight focused tests and Ruff pass. No-trade/cash-control values and matching attributes are caller supplied; the matcher does not construct the counterfactual portfolio from the paper ledger or bind those inputs to an authoritative evidence registry. It produces no confidence interval or promotion claim. A matched difference cannot establish that filtering or dropping losing trades improves performance.

Evidence and source hashes are recorded in `pe-0136-matched-opportunity-controls-evidence.json`.
