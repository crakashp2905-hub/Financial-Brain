# Cost-aware paper rebalancing primitive

Date: 2026-11-25

`portfolio.rebalance.plan_rebalance` creates a deterministic paper-only rebalance plan
from current/target NAV weights, per-instrument no-trade bands, proportional cost rates,
fixed per-order INR charges, and horizon-matched expected-improvement estimates. A line
inside its band does not trade. Outside the band, a proposed change is admitted only when
the supplied expected improvement exceeds proportional plus fixed charges. Rejected
lines retain the intended turnover and cost estimate for audit while the proposed portfolio
keeps its current weight. Output shows aggregate turnover, cost, expected net improvement,
per-line dispositions, and the resulting virtual weights.

Verification: `tests/test_portfolio_rebalance.py` reports 3 passed; Ruff and targeted mypy
pass.

**Status: partial.** The planner does not submit or simulate fills. Fixed charges and cost
rates are caller-supplied rather than sourced from a current venue/broker schedule. It does
not yet account for shared cash/margin, taxes, lots/ticks, settlement, portfolio-level
cross-asset constraints, or runtime integration with the paper books.
