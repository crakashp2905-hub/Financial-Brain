# ETF terminal evidence panel v1

The terminal now renders one ETF row from already resolved point-in-time inputs:

- point-in-time benchmark label from the ETF reference record;
- executable quote bid/ask and its source/event time;
- quote-to-iNAV premium/discount result from `features.etf_valuation.compare_quote_to_inav`;
- effective tracking disclosure from `ETFReferenceCatalog.resolve_tracking_metrics`;
- cost/liquidity estimate from `features.etf_liquidity.analyze_etf_liquidity`.

The `etf_rows` input to `paper.terminal.build()` is deliberately an adapter seam. Missing
values render as unavailable; the premium is only rendered when a comparison result
contains an iNAV age, and no NAV or fill is synthesized. With no rows, the terminal
explains that permissioned source data is not connected.

## Remaining for full checklist acceptance

The repository does not yet persist ETF reference/valuation/tracking snapshots in the
terminal database or assemble `etf_rows` from a permission-cleared live/replay feed.
Therefore this UI integration is **PARTIAL**, not complete. Do not pass quote data until
its actual data-use rights are reviewed; the checklist's rights register currently
does not establish that a paid market-data account permits this storage/display use.
The cost field is the existing estimate and must not be represented as a calibrated
execution cost. Prospective paper evidence and benchmark coverage remain separate work.

Focused coverage: `tests/test_etf_terminal_panel.py` checks field display, missing-data
behavior, and the empty-feed state.
