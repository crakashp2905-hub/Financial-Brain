# Physical-order routing lock audit — 2026-11-23

Checklist item: **PA-01-16** — “Keep live broker order routing physically disabled at build, config, CLI, API and integration-test layers throughout the entire profitability program.”

## Verified lock points

- **Build and broker transport:** `src/financial_brain/execution/order_policy.py` sets `PHYSICAL_ORDERS_ENABLED = False`; `deny_physical_order` always raises and has no runtime enable switch. `KiteBroker._request` calls it for every non-GET before credentials or network transport.
- **Configuration and session mode:** `product-charter.v1.json` and `launch-gates.v1.json` both set physical orders false. `resolve_mode(LIVE_CASH)` rejects unconditionally, and every `SessionMode` reports `may_send_physical_order=False`. Forged environment variables, compliance, approvals, rollout stage and a monkeypatched true flag do not change that result.
- **CLI:** forward CLI commands state `broker_orders=False` or `physical_orders_enabled=False`; there is no broker-order command path.
- **API:** the HTTP API exposes decision compilation/challenge routes only. New `tests/test_api_broker_isolation.py` parses API modules and rejects physical broker adapter imports, order operations, or POST/PUT/PATCH/DELETE routes under `/orders` or `/broker`.
- **Integration probes:** central legacy OMS tests force rollout, environment and monkeypatched policy flags open, then prove `place`, `cancel`, and all write HTTP verbs are refused without calling the transport. Forward paper and intraday tests forge live flags and assert simulated fills occur with zero broker/network calls. A source guard keeps the write-capable request builder isolated to the locked Kite broker module.

## Verification

Focused negative-path suite: **9 passed** across API isolation, Kite broker lock, legacy OMS lock, auto-submit lock, source-path isolation, execution-mode lock, forward paper, intraday paper/replay and CLI fail-closed behavior. Product-charter, launch-gate and forward-engine graph validators pass; Ruff and `git diff --check` pass.

**Status: COMPLETE** for the paper profitability program's physical-order-disabled invariant. This is not a statement that a future live-trading release is approved; any such change requires a separately reviewed policy and checklist revision.
