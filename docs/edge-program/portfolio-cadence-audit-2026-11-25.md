# Portfolio rebalance cadence and risk-exit audit

Date: 2026-11-25

`portfolio.cadence.evaluate_cadence` joins a frozen, source-hashed schedule of portfolio
rebalance times with already-emitted, typed `EmittedExitEvent` risk exits. It only emits
scheduled review actions between the frozen caller cursor and the as-of time; event exits
retain their observed-trigger and event timestamps, position identity, source identity and
idempotency key. Risk exits sort ahead of a scheduled review at the same timestamp. A
deterministic audit digest binds the policy and actions. The module never fabricates an exit
signal or submits an order.

Verification: `tests/test_portfolio_cadence.py` reports 4 passed. Ruff and targeted mypy
pass.

**Status: partial.** Exchange-session schedule sourcing, durable cursor persistence,
portfolio runner/OMS dispatch, missed-exit escalation, event-to-risk policy evaluation,
shared book closure and prospective operation remain unwired. Input schedule hashes prove
identity, not exchange-calendar correctness or source rights.
