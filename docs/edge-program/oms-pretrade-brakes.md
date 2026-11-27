# OMS pre-trade brakes

The legacy OMS rejects a new order unless its pre-trade snapshot includes a fresh exchange quote, a local receipt
timestamp, an approved source identity, and current daily account P&L. It also enforces daily order count and turnover,
per-order and per-position value, gross exposure, duplicate-order and order-rate limits, and a cap on prior failed or
unresolved executions. The same quote and account-loss checks run again when a human approves a ticket.

Kite quote snapshots use the full quote response's exchange `timestamp` and local receipt time. The exchange timestamp
is the quote packet time; `last_trade_time` is deliberately not used as a substitute because a book update can arrive
without a new trade. PaperBroker records simulation-event time separately from local receipt time. A missing, malformed,
future, or stale timestamp blocks the order.

`OMS` takes `account_daily_pnl_provider(day)`. The provider must read the reconciled paper account's daily P&L. It must
not use strategy signal output or values supplied by a strategy. If no provider is configured, the OMS rejects orders
because an absent account-loss snapshot is not evidence that the loss limit is clear. The existing `trading.rehearse`
and ticket helpers accept provider injection, but the repository does not yet supply a durable, reconciled provider for
the legacy OMS. Its loss brake therefore remains operationally incomplete and checklist PA-13-07 remains `PARTIAL`.

The default small paper limits use a 5-second quote-age ceiling, three failed/unresolved executions per session, and a
Rs 2,500 daily loss limit (Rs 1,250 for the smaller candidate tier). These are paper controls and do not authorize
physical broker orders.
