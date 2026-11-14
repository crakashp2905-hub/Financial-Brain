# PE-0119 — Cohort performance summary

The new report computes net annual return, volatility, Sharpe, Sortino, maximum drawdown and profit factor. It also reports mean daily alpha from the matched benchmark stream and one-way turnover when those inputs are supplied. Every report exposes its sample dates, calendar duration, annualization/risk-free assumptions and scheduled denominator. Missing sessions remain in the denominator and withhold metrics that need a complete return path; unscheduled observations are rejected.

**Status: DONE for the reporting contract.** The tests cover complete reports, exact denominator and assumptions, cash/zero observations, missing-session withholding and unscheduled-data rejection. Source NAV, costs and matched benchmark provenance remain upstream requirements.

Evidence and source hashes are in `pe-0119-performance-summary-evidence.json`.
