# PE-0051 target portfolio planner

`plan_target_portfolio` builds a long-only research/paper target from externally supplied expected net alpha and same-holding-period covariance. It uses a deterministic capped-simplex projected optimizer with a quadratic risk penalty, one-way turnover cost, per-name cap, explicit CASH residual, configured cash floor, and one-way turnover limit. Rebalances occur only on a frozen date set; non-scheduled observations retain the previous book exactly. Inputs and prior holdings outside the frozen universe fail closed.

`cash_days` counts target observations whose explicit CASH weight is above a stated epsilon. This is exposure accounting, not a profitability statistic. The planner does not generate alpha, fetch market data, or send broker orders. Output is only as good as the supplied forecasts, covariance, cost units, and schedule; no Indian-market outcome is claimed.
