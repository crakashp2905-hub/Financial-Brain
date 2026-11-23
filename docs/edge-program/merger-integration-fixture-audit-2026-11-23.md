# Merger integration fixture audit — 2026-11-23

Checklist item: **PA-01-11** — “Add merger integration fixtures exercising all five desks, existing zoos, closed trade reconciliation, event custody, and negative physical-order probes.”

## Evidence found in the current checkout

The repository has separately tested components that cover each requested area:

| Acceptance area | Existing evidence | Limit |
| --- | --- | --- |
| Swing | `tests/test_phase5_swing.py` covers next-session entry, costs, stops and clustered-date inference. | No merger-level fixture. |
| Intraday | `tests/test_phase5_intraday_runner.py::test_live_view_tracks_open_then_closed_trades_without_a_database_reader` and adjacent session tests cover open/close behavior. | Separate runner and paper ledger. |
| Options | `tests/test_forward_edge_options_spread.py::test_vertical_spread_uses_entry_and_exit_bid_ask_and_fees`; `tests/test_paper_options_runner.py` covers later entry and observed exits. | Not joined to a shared multi-desk account fixture. |
| ETF | `tests/test_etf_execution.py::test_fill_uses_quote_side_and_observed_depth_only` covers quote-side pricing and depth-limited fills. | Separate ETF execution resolver. |
| Mutual funds | `tests/test_mf_order_book.py` covers request identity, acknowledgements, permission and lifecycle rules. | Scheme-day order lifecycle remains separate from exchange desks. |
| Existing strategy zoos | `tests/test_phase5_zoo.py` covers pairs, carry, martingale and Sharpe allocation; `tests/test_phase5_zoo_edges.py` covers zoo edge metadata. | Not routed through a single merger fixture. |
| Closed-trade and accounting | `tests/test_forward_edge_session_runner.py::test_paper_accounting_replays_sell_inventory_and_gross_realized_pnl`, `tests/test_forward_edge_ledger_reconcile.py::test_daily_after_cost_portfolio_pnl_reconciles_to_ledger_accounts`, and the intraday close tests. | Components use separate stores and fixtures. |
| Event custody | `tests/test_decision_manifest.py`, `tests/test_forward_edge_market_events.py`, `tests/test_forward_edge_f00.py`, and `tests/test_decision_journal.py` cover immutable decision manifests, event clocks/provenance, custody input requirements and journaling. | No end-to-end custody assertion spanning each desk in a merger fixture. |
| Negative physical-order probes | `tests/test_oms.py::test_shared_physical_order_lock_blocks_legacy_writes_even_when_every_legacy_lock_is_open`, `tests/test_oms.py::test_oms_auto_submit_cannot_escape_physical_order_lock`, and `tests/test_forward_edge_session_runner.py::test_forward_paper_intent_cannot_reach_broker_with_forged_live_flags`. | Probes are isolated; no common merger fixture asserts the invariant across all desk paths. |

## Assessment

The requested component-level test material is present, but there is no single integration fixture that wires the five desk flows and strategy zoos through shared event custody, reconciles completed trades, and asserts that physical order submission is impossible across the combined system. The charter still labels each desk implementation as `SCOPE_ONLY`, and the desk engines do not share one end-to-end account/event path. Calling the existing isolated tests a merger fixture would overstate what they prove.

**Status: PARTIAL.** The remaining acceptance work is to build a deterministic integrated fixture (or fixtures with a shared merger harness) that executes those paths together and proves closed-trade reconciliation, event custody and the negative physical-order invariant at the integration boundary.

No profitability, production readiness, or live execution claim follows from the component tests listed here.
