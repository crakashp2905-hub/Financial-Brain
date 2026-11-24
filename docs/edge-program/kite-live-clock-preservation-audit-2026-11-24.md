# Preserve Kite source and exchange clocks through ingestion adapter

Date: 2026-11-24  
Checklist item: FB-0105  
Status: PARTIAL

## Change

The read-only Kite ingestion adapter previously normalized source and exchange timestamps, then rebuilt the event without forwarding those fields. The adapter now carries `source_at`, `exchange_at` and any supplied `published_at` into the emitted event. Receipt and availability remain explicit and are not substituted for source clocks.

## Evidence

- `src/financial_brain/ingest/kite_live.py::forward_events`
- `tests/test_kite_live_forward_events.py::test_forward_events_preserves_executable_depth_ohlc_status_and_source_hash` checks all available clocks survive the adapter and serialization round-trip.
- `python -m pytest tests/test_kite_live_forward_events.py tests/test_forward_edge_kite_market_data.py -q --basetemp=.pytest-checklist-kite-clocks` — 22 passed.
- Ruff and scoped mypy passed for the adapter and test module.

## Remaining scope

FB-0105 remains PARTIAL. This Kite path does not receive an independent publication timestamp from the provider; it leaves that field unset. Other provider, filing, NAV, replay and export paths still require a repository-wide timestamp-lineage audit. Historical missing timestamps cannot be reconstructed.
