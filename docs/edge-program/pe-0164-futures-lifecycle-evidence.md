# PE-0164 — Futures roll, variation and overnight carry

## Acceptance

The futures paper account uses sourced contract terms, including expiry, lot size and
multiplier. Daily variation and final expiry settlement are posted to the durable ledger.
An open-interest roll planner requires source-hashed front/next OI observations, waits for
a configurable confirmed crossover, and forces a roll decision before front expiry.
Funding is computed from marked contract notional, an explicit annual rate and calendar
days, then posted as a durable cash expense or credit. Replayed account balances must
reconcile after restart.

## Implementation and verification

`financial_brain.evaluation.futures_roll_schedule.build_roll_schedule` emits deterministic
HOLD/ROLL/BLOCKED decisions and hashes both policy and OI evidence. It is a paper planning
input; contract selection and execution prices remain separately sourced inputs.

`FuturesPaperAccount.fund_overnight` records a source-hashed funding assumption and posts
the calculated amount to cash and funding expense. `variation_pnl`, `funding_pnl`, and
`net_pnl` reconcile across a roll to a differently sized multiplier and final expiry.
Duplicate funding for one session is rejected; identical event replay is idempotent.
Existing sourced contract terms and settlement paths remain in force.

Four new lifecycle tests and the existing futures paper and carry tests pass (19 total);
Ruff passes. Source inventory verification passes for 613 Python files. Fixtures are
synthetic. Actual NSE OI, funding rates, broker fees and forward P&L have not been measured.

## Status

DONE for the software lifecycle acceptance. Funding rate and marked notional are explicit
scenario inputs rather than a provider financing quote. Trading/roll execution fees remain
separate cost inputs and must be applied by the broader paper-cost model before any net
profitability conclusion.
