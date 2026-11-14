# PE-0135 — gross losses versus cost-erased trades

`gross_cost_report()` separates trades whose gross P&L was negative from trades with positive gross P&L that costs reduced to zero or below. It also reports profitable and gross-flat rows so the classification denominator is visible. Each group includes trade count, gross/cost/net INR, share of total traded notional, and gross/cost/net basis points weighted by the group’s total notional. The daily paper report now includes this breakdown for each evidence mode.

**Status: DONE** for the stated reporting criterion. Six focused tests (including existing daily-report tests) and Ruff pass. The report only includes closed rows already present in the caller’s ledger and validates `net = gross - costs`; it does not infer future profitability or include open positions.

Evidence and source hashes are recorded in `pe-0135-gross-loss-cost-erasure-report-evidence.json`.
