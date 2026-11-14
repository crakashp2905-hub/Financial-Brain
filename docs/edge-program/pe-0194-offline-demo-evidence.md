# PE-0194 — Complete offline onboarding demo

## Acceptance

One command reproduces a win, loss, `NO_TRADE`, feed outage, and `UNKNOWN_ORDER` with clear evidence labels.

## Implementation and verification

Added the `offline-demo` command. It runs the same canonical session pipeline and fixture pack used by existing CLI scenarios, closes each record, verifies expected outcomes and accounting integrity, and emits one deterministic JSON report. The operator guide now includes the command. Every row is labeled `SYNTHETIC_FIXTURE` and `LOCAL_ONLY`, shows `physical_orders: false`, and the report says it is not promotion-eligible.

Verification: fixture replay and F13 CLI suites — 56 passed; Ruff, targeted mypy, golden signature coverage, and source-inventory check passed.

## Limits

All data are synthetic and the demo runs offline. It demonstrates the engine paths and labels only; it does not demonstrate live feed quality, execution, or profitability.
