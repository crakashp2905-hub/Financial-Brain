# Effective-dated Indian cash-equity cost schedule

`forward_edge.execution.CostRule` accepts owner-supplied effective-date rates for brokerage, exchange transaction fees, STT by side, buy-side stamp duty, GST, SEBI turnover fees, IPFT, and clearing charges. Brokerage minimum/maximum rupee bounds are configurable. Inputs are validated, and `CostSchedule.sha256` identifies the complete effective-dated table so a result can cite the exact fee schedule used.

Charges are computed from unrounded components and the total is rounded once to paise. Component amounts are reported separately. Buy and sell legs are calculated independently so a round-trip sum includes each side's statutory charges and taxes.

No current legal/broker rates are bundled or inferred by the code. The caller must provide a source-verified rate table and effective dates. The helper is not yet wired into the `PaperAccount` bridge, which currently charges its separate effective-dated owner-supplied all-in BPS schedule. Until that integration and the owner-sourced rates exist, this is implementation evidence only and must not be treated as a complete rupee-cost/P&L bridge.
