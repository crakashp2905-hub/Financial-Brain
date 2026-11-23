# Deterministic delayed fixture replay audit — 2026-11-24

Checklist item: `PA-02-13` — provide low-cost deterministic synthetic and delayed/replay feed paths while keeping fixture observations distinguishable from real market data.

## Implemented and verified

- `forward_edge.fixtures.fixture_pack()` supplies eight explicit synthetic cases with frozen outcomes. `test_forward_edge_pipeline_compat.py` protects the full outcome/signature map and repeat-run determinism through the canonical paper runner.
- Added `market_data.delayed_fixture_replay(events, delay=...)`. It accepts only explicitly fixture-labeled `MarketEvent` sources, refuses Kite/provider events, leaves source event clocks intact, shifts receipt and strategy-availability clocks by the declared delay, sorts by availability/sequence, and assigns a visibly synthetic replay source.
- Each replay row gets a deterministic replay event ID and evidence SHA-256 over the original event digest, replay schema, and exact delay. The fixture evidence class remains `DETERMINISTIC_FIXTURE` even when market-observed flags are passed.
- Verification: `tests/test_forward_edge_fixture_replay.py`, `tests/test_forward_edge_pipeline_compat.py`, `tests/test_forward_edge_kite_market_data.py`, and `tests/test_forward_edge_market_events.py` — 47 passed. Scoped Ruff, mypy, and diff checks passed.

## Remaining limits

Status remains `PARTIAL`. The replay helper is an engineering API and is not exposed as an operator-facing CLI/dashboard mode; no UI rendering test proves the synthetic marker is visible in every consumer. These fixture paths validate plumbing and timestamp behavior only, not market performance or profitability.
