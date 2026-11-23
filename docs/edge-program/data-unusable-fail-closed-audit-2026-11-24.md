# Data unusable and fail-closed audit — 2026-11-24

Checklist item: `PA-02-16` — expose `DATA_UNUSABLE`, reject missing/poor-quality market data, and never forward-fill a tradable quote.

## Implemented and verified

- `forward_edge.data_gate.evaluate_market_data` already returns `NO_TRADE` with stable detail codes for missing quotes, stale/future/naive clocks, invalid bid/ask, excessive spread, zero volume, circuit/freeze limits, absent auction data, non-continuous phase, and unknown or denied data rights.
- Added `DataGateResult.data_status`, which emits `DATA_UNUSABLE` for any rejected observation while preserving the detailed reason tuple; eligible observations emit `USABLE`.
- The durable `ForwardSessionRunner` requires a journaled source event, availability no later than simulated execution, an in-age executable two-sided quote with visible depth, immutable evidence hash, and accounting reconciliation. It refuses absent or stale quote data; it does not substitute a carried-forward price.
- Verification: `tests/test_forward_edge_data_gate.py`, `tests/test_forward_edge_integrity_gates.py`, and `tests/test_forward_edge_pipeline_compat.py` — 26 passed. Scoped Ruff and mypy passed.

## Remaining limits

Status remains `PARTIAL`. The stable coarse status is exposed by the offline gate, while the durable strategy candidate path still reports detailed runner errors or `WITHHELD` reason codes rather than consistently attaching `DATA_UNUSABLE` to every failed candidate. Coverage across all legacy adapters, strategies, and outputs has not been demonstrated.
