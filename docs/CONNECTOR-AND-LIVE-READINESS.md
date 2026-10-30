# Connector architecture and live-readiness gates

Financial Brain should be a decision and risk system with replaceable connectors, not a broker-bound trading bot. The existing `KiteBroker` is the only order-sending adapter; `PaperBroker` remains the default. No TradingView alert, agent, MCP tool, or research result may bypass the OMS locks, human stage controls, risk gate, order journal, and reconciliation.

## Connector roles

| Integration | Role now | Boundary |
| --- | --- | --- |
| Zerodha Kite Connect | Authorized historical/live data, account observations, paper evidence; staged order adapter already exists | Never infer fills from a placement response. Reconcile orders, trades, cash, positions, and holdings with broker records. |
| TradingView | Optional charting and alert observation source | Alerts are untrusted, potentially delayed/duplicated/lost, not authoritative prices or executable orders. No scraping or bulk data extraction. |
| Other brokers/data vendors | Future adapters only when data rights, reliability, and test fixtures are established | Use the same normalized event/OMS contracts; vendor-specific margin and settlement rules stay explicit. |

`interfaces/tradingview_alert.py` implements only bounded schema and freshness validation for an **already authenticated** alert. It deliberately has no HTTP listener and cannot submit an order. A future webhook ingress needs HTTPS, an application-level authentication design, rate limiting, request logging without secrets, replay/deduplication by alert ID, persistent raw/normalized evidence, and acknowledgment within TradingView's timeout. Its output should be considered a candidate observation and independently checked against authorized Kite quotes before paper evaluation. Do not place a credential in a TradingView alert body.

## Ordered implementation and acceptance gates

1. **Data/evidence:** Archive contract masters and authorized quote/depth snapshots with event time, receive time, source, instrument identity, completeness, and content digest. Reject stale/missing observations. Build daily coverage reports and immutable off-machine receipts. For expired options, collect prospective history while contracts are live; Kite does not supply historical expired-option candles.
2. **Research:** Resolve delistings, suspensions, corporate actions, and missing outcomes. Freeze universe/strategy/cost assumptions and register future cohorts. Keep the protected holdout and exhausted 2026Q4 trial budget closed. Require complete paired champion/challenger reports and capacity/cost sensitivity before promotion.
3. **Paper execution:** Replay broker snapshots and websocket order updates including duplicate, delayed, missing, rejected, partial, and out-of-order events. Reconcile actual order/trade book and positions; prove idempotent recovery after process restart. Measure implementation shortfall, fill rate, and signal decay. Test broker charge/margin estimates against observed statements.
4. **Options:** Version instrument/lot/expiry rules; model missing legs, synchronized fills, assignment/settlement, liquidity, and broker-calculated basket margin. The paper debit-spread payoff envelope is not a broker margin approval.
5. **Operational readiness:** Enforce per-module CI and dependency lock/matrix; restore from backup in a timed drill; test kill switch, stale-data stop, loss limits, cash/position mismatch stop, and notification delivery. Conduct trader task studies and document failure states in the UI.
6. **Separate live-capital review:** Obtain broker confirmation of API/retail-algo requirements, data use, static-IP or other current controls, strategy registration if applicable, and actual account permissions. Only a human may approve a time-bounded ticket-stage pilot. No automatic promotion; any later scale needs prospective evidence and another review.

Passing tests does not establish positive net alpha. Live mode remains closed until the externally verifiable prospective evidence and operational/regulatory gates pass.

## Primary specifications

- [Kite orders](https://kite.trade/docs/connect/v3/orders/): placement returns an ID, not an execution guarantee.
- [Kite WebSocket](https://kite.trade/docs/connect/v3/websocket/): live quotes/depth and order updates.
- [Kite historical](https://kite.trade/docs/connect/v3/historical/): continuous expired-futures day bars, not expired-option history.
- [Kite margin/charges](https://kite.trade/docs/connect/v3/margins/): order/basket margin and charge estimates.
- [TradingView webhooks](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/): delivery and timeout limitations.
- [SEBI retail algo circular](https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html): verify current applicability with the broker before any live stage.
