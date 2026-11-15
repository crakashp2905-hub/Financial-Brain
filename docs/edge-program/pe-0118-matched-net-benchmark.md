# PE-0118 — Same-date, same-risk net benchmark

`matched_performance.from_nav` converts after-cost account NAV and external cash flows into daily returns, including cash days. `matched_excess` refuses missing or duplicate dates and requires strategy and benchmark to use the same declared INR risk budget for every paired session. It returns the paired daily net alpha observations and exact denominator.

**Status: DONE for the matched-return calculation contract.** The focused tests cover flow adjustment, cash days, aligned sessions, mismatched calendars and mismatched risk budgets. The helper requires NAV already include real costs and marks; no market performance or profitability is claimed.

Evidence and file hashes are in `pe-0118-matched-net-benchmark-evidence.json`.
