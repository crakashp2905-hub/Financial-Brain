# Discrete portfolio capacity optimizer

Date: 2026-11-25

`portfolio.capacity_optimizer.optimize_capacity` proposes buy-side paper allocations in
integer whole-share lots. The mixed-integer objective maximizes expected INR P&L after
caller-supplied variable costs, transaction taxes, positive-gain tax assumptions, fixed
per-order charges, and leverage financing. Constraints cap per-candidate quantities, gross
notional/leverage, aggregate margin, and cumulative cash requirements by every settlement
date. Cash is reserved conservatively: variable fees and taxes plus fixed charges are
reserved on the first settlement date; modeled financing is reserved by the final settlement
date. The cost, tax, settlement, margin and financing inputs must share a source hash and
as-of timestamp. The module has no broker or provider access and never creates orders.

Verification: `tests/test_portfolio_capacity_optimizer.py` reports 4 passed. Ruff and
targeted mypy pass.

**Status: partial.** No Indian exchange/broker fee, tax, settlement-calendar, haircut or
financing schedule is embedded or independently sourced. Positive-gain tax is a caller
assumption, not tax advice. The optimizer supports long-only share lots; derivative lots,
short-sale proceeds, cross-currency settlement, settlement holidays, corporate actions,
credit facilities, dynamic margin add-ons, portfolio/OMS integration, and prospective
paper validation remain unimplemented. Source hashes establish input identity only; they
do not establish permission, accuracy, or tax applicability.
