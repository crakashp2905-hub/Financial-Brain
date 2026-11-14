# TradingView charting integration decision

Financial Brain already renders per-symbol research pages with TradingView **Lightweight Charts** in `paper/charts.py`. That is a different product from TradingView **Advanced Charts / Charting Library**. Keep Lightweight Charts as the default. Add Advanced Charts only after approved access to TradingView's private repository and a license review. Never copy its assets into this public repository or obtain them from third parties.

## Proposed boundary

```
Kite authorized candles/quotes -> Financial Brain archive -> read-only chart datafeed
                                                          -> licensed Advanced Charts UI
research decisions/positions -> reviewed annotation API  -> chart overlays
human action -> existing staged OMS -> Kite (never through chart callbacks)
```

The chart is a view, not an execution authority. Its datafeed must use the same instrument identity, timestamps, adjustment policy, and freshness labels as the paper engine. Overlays must distinguish observed fills, simulated fills, and hypothetical signals. A stale chart must visibly fail closed rather than silently presenting an old bar as live.

## Implementation contract after license access

1. Use a private, deploy-time asset path outside the public repo. Record library version, entitlement and license in a release manifest; CI uses a stub.
2. Serve a read-only datafeed. `interfaces/chart_datafeed.py` and `chart_udf_api.py` now implement a bounded archive-backed UDF subset (`/config`, `/symbols`, `/search`, `/history`) for one-minute bars. The app is not started automatically, and must be bound to localhost or protected by an authenticated gateway. For realtime charts implement `subscribeBars`/`unsubscribeBars` with independent subscriber IDs. Fan out authorized streams; do not query Kite for every browser request.
3. Resolve symbols by exchange, instrument token/contract, expiry, lot size, tick size, session and `Asia/Kolkata`. Never join recycled option tokens to current contracts. Label raw versus adjusted bars; execution charts use tradable raw prices.
4. Bound history requests, return complete ascending OHLCV bars with UTC epoch timestamps, and distinguish no-data from source failure. Use `minute_bars` only where coverage exists; do not synthesize missing candles. Track event-time and receive-time freshness.
5. Publish non-sensitive annotations with source/version/time provenance: setup, invalidation, expected net after costs, evidence tier, paper order/fill state and risk flags. Disable chart trading controls; a chart click cannot bypass OMS approval.
6. Test IST session edges, no duplicate/out-of-order bars, concurrent subscriptions, gap/reconnect recovery, corporate-action and contract-roll correctness, stale-source banners, ledger reconciliation, and license/redistribution controls.

Current limits: UDF only serves archived one-minute Kite bars, up to seven days and 10,000 bars per request. No realtime subscription, expired-option contract resolver, external authentication, or licensed UI assets are included. The archive's `minute_bars` table lacks an exchange/contract-type field, so `KITE:<instrument_token>` is a local archive key, not a complete tradable identity. Ambiguous recycled tokens fail closed; add a versioned contract master before supporting derivative charts. Historical display alone is not evidence that the latest candle is fresh.

Advanced Charts supplies the UI, **not market data**. Kite remains the authorized data source, subject to broker and exchange terms. Charting does not prove alpha or permit live trading.

## API-reference coverage (Charting Library and Datafeed only)

The [API reference](https://www.tradingview.com/charting-library-docs/latest/api/) is a catalog of thousands of types, including trading-platform types. Financial Brain uses the relevant **interfaces**, not every enum or drawing override. Broker API and Trading Platform order interfaces are expressly excluded.

| Interface family | Current adapter | Remaining requirement |
| --- | --- | --- |
| Charting Library widget | `advanced_charts_widget.mjs` constructs a widget, awaits `chartReady()`, exposes `activeChart()`, theme change and disposal | Approved private library assets and an actual browser integration test |
| Datafeed historical/symbology | `advanced_charts_datafeed.mjs` implements `onReady`, `resolveSymbol`, `searchSymbols`, `getBars` against the read-only UDF service | Complete contract identity, more intervals, corporate-action policy, longer-range paging |
| Datafeed live bars | `subscribeBars`/`unsubscribeBars` are intentionally inert for this archive-only feed | Authenticated Kite WebSocket fanout, freshness/sequence checks and reconnect tests before enabling live status |
| Quotes, depth, news, custom indicators, marks/drawings, saves/layouts | Not enabled | Each needs a licensed and tested data/annotation service, permissions, and appropriate source rights |
| Broker API / Trading Platform orders | Not used | Remain outside the chart integration; existing OMS and its human gates stay authoritative |

The JavaScript adapter is original project code; it does not contain TradingView's private library. CI runs its contract tests with Node, using a mocked widget. No bundled asset or completed visual/browser QA is claimed.

## Sources

- [TradingView Advanced Charts quick start and access terms](https://www.tradingview.com/charting-library-docs/latest/quick-start/)
- [TradingView Datafeed API](https://www.tradingview.com/charting-library-docs/latest/connecting_data/datafeed-api/)
- [TradingView UDF adapter](https://www.tradingview.com/charting-library-docs/latest/connecting_data/UDF/)
- [TradingView realtime subscriptions](https://tradingview.com/charting-library-docs/latest/connecting_data/datafeed-api/datafeed-subscriptions/)
