# Cross-desk portfolio risk report primitive

Date: 2026-11-25

`portfolio.cross_desk_risk.analyze_cross_desk_risk` now computes one report over
explicit signed INR position exposures. It reports gross/net exposure, long/short
position HHI, signed factor exposures and factor concentration, current and shocked
portfolio volatility, Euler marginal risk contributions, and historical expected-shortfall
and VaR losses for separate current and shocked scenario panels.

The function rejects incomplete/duplicate covariance labels, asymmetric or non-PSD
covariance matrices, incomplete factor mappings, non-finite values, invalid NAV/confidence,
scenario label mismatch, and fewer than 20 scenario rows. Covariance horizon and scenario
return horizon must be consistent at the call site. It does not invent stressed scenario
returns from correlation matrices: each scenario panel must be created by an independently
versioned and source-audited adapter.

Focused verification: `tests/test_cross_desk_risk.py` plus cross-desk overlap and canonical
ledger suites report 18 passed. Ruff and targeted mypy pass.

**Status: partial.** This is a deterministic calculation layer, not an account risk report.
No runtime adapter yet joins H005/H003/P3, options Greeks, ETF/MF holdings, holdings-source
rights, current/stress covariance, and matched historical/stress scenarios. Do not interpret
fixture output as a risk estimate for a real or paper account.
