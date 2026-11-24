# Cash-management allocation scenarios v1

`features.cash_management.compare_cash_alternatives()` compares a zero-return cash
control with caller-supplied liquid-fund, overnight-fund, debt-fund and ETF scenarios.
Each alternative requires source identity, source hash, publication/observation/
availability clocks, business-day payout lag, and an explicit tax assumption before it
can be allocated.

The score is an input-driven scenario calculation:

```text
net basis points = annualized yield × holding business days / 252
                   − estimated tax on positive scenario yield
                   − subscription, redemption and exit-load costs
                   − caller-supplied stress-loss and liquidity haircuts
```

Cash remains a 0 bp control. A product is not allocated if its evidence is stale or
future-dated, payout lag exceeds the intended holding period, tax is unknown, its net
scenario does not beat cash, or its stress haircut is zero/invalid for the allocation
budget. The result reserves owner-configured cash, applies per-product capacity and caps,
and never creates a fund unit or transaction. Each complete scenario is content-hashed.

This advances FB-0606 to **PARTIAL**. It does not fetch a permissioned product universe,
estimate yields, establish India-specific tax treatment, calibrate liquidity/credit
stress, or demonstrate prospective after-cost value. Input assumptions and stress
haircuts are not forecasts, VaR, or probabilities. No real allocation recommendation is
made without those external facts and validation.

Focused tests cover the cash control, after-cost ranking, stale/future data, settlement
lag, missing tax, risk-budget sizing, and duplicate identities.
