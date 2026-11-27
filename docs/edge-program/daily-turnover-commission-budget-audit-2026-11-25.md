# Daily turnover and commission budget gate

Date: 2026-11-25

`portfolio.daily_budgets.evaluate_daily_budgets` evaluates account/scope-level daily gross
turnover and commission ceilings as two independent limits, separate from any candidate's
per-trade expected-cost hurdle. It starts from source-hashed existing reservations, then
considers new source-hashed orders by frozen priority and stable order ID. An order is
accepted only when both budgets fit; rejected orders consume neither. The result reports
both used/remaining budgets and a content hash over the policy, reservation/proposal
evidence, and ordered decisions. Decimal INR arithmetic avoids binary-float budget drift.

Verification: `tests/test_portfolio_daily_budgets.py` reports 4 passed. Ruff and targeted
mypy pass.

**Status: partial.** The gate is a pure proposal and does not atomically persist reservations;
concurrent desk calls can oversubscribe unless a durable shared book serializes them. It
does not compute source commissions, reconcile exchange fills/cancels, or implement
turnover netting rules across cash/F&O products. Runtime/OMS integration and prospective
paper validation remain outstanding.
