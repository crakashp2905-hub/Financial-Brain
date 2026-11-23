# PR #40 versus `main`: public contract reconciliation

**Snapshot:** PR #40 head `39817e98dea70640c26d7530dcf51a23d0cce2bb`; `main` at `3c99b5dddc775db888822b4cec1849e5ebca56c8`. PR #40's merge commit is an ancestor of this `main`. This is a source-contract reconciliation, not a profitability, runtime-integration, or validation-transfer claim.

**Actions:** `KEEP` retains the same canonical owner contract; `MERGE` means the main-branch implementation has evolved and the reconciliation keeps the PR #40 contract while accepting tested additive main changes. `REIMPLEMENT` would rebuild a missing/unsafe public contract; `REJECT` would retire a superseded contract. No full contract is classified `REIMPLEMENT` or `REJECT`: PR #40 is already in main, and removing any existing scope requires behavioral evidence. Alternative owners remain scoped as noted rather than silently removed.

| Public contract | PR #40 owner blob → main owner blob | Decision | Reconciliation and boundary | Relevant current tests |
|---|---|---|---|---|
| `operator_cli` | `forward_edge/cli.py` `639b482e` → `639b482e` | **KEEP** | CLI contract is byte-identical. It still does not start prospective sessions from live Kite observations; archived replay is not forward evidence. | `tests/test_forward_edge_cli.py` |
| `prospective_session_lifecycle` | `forward_edge/session_runner.py` `6d434633` → `1e07d305` | **MERGE** | Main retains the same `ForwardSessionRunner` lifecycle and adds an optional `quantity_unit` input to paper-intent execution. Preserve PR #40 durable lifecycle semantics and main's instrument-aware extension; live Kite-to-runner launch and independent timestamp custody remain open. | `tests/test_forward_edge_session_runner.py` |
| `legacy_intraday_paper` | `paper/intraday_runner.py` `6f9a0017` → `6f9a0017` | **KEEP** | Existing read-only Kite poller and legacy paper book remain a distinct source path; do not treat it as an adapter or substitute for prospective runner evidence. | `tests/test_phase5_intraday_runner.py` |
| `broker_oms` | `execution/oms/engine.py` `980bf82d` → `980bf82d` | **KEEP** | Broker OMS remains separate from simulated paper execution. Its existence does not enable physical orders or qualify paper fills as broker fills. | `tests/test_oms.py` |
| `simulated_forward_execution` | `forward_edge/session_runner.py` `6d434633` → `1e07d305` | **MERGE** | Preserve PR #40 quote eligibility and simulated receipt boundary with main's later execution scenario, market-data and durable-journal changes. Option-intent simulation remains a separate contract, not proof of a complete option lifecycle. | `tests/test_forward_edge_execution_scenario.py`, `tests/test_forward_edge_session_runner.py` |
| `risk_policy_and_sizing` | `risk/allocator.py` `cf6c7f30` → `cf6c7f30` | **KEEP** | Retain the research sleeve allocator and the forward candidate risk gate as separate scopes. Neither alone owns end-to-end capital allocation across books. | `tests/test_forward_edge_risk_allocator.py` |
| `strategy_identity_and_lifecycle` | `forward_edge/registry.py` `2415ce1f` → `2415ce1f` | **KEEP** | Frozen strategy eligibility/version lifecycle remains canonical. AlgorithmSpec identity synchronization is still unresolved; do not merge the registries by name alone. | `tests/test_forward_edge_registry.py` |
| `prospective_promotion_evidence` | `evaluation/prospective.py` `77c22273` → `77c22273` | **KEEP** | Forward-only champion-pick evidence remains separate from statistics and governance. No module alone owns the complete promotion decision. | `tests/test_prospective_champion.py` |
| `forecast_path_distribution` | `forecasting/distribution.py` `cb9fece1` → `cb9fece1` | **KEEP** | Keep the validated sampled-path output contract distinct from model generation, calibration and validation. | `tests/test_phase5_forecasting.py`, `tests/test_phase5_walkforward.py` |
| `canonical_portfolio_accounting` | `portfolio/canonical_ledger.py` `ad87d5fa` → `ad87d5fa` | **KEEP** | Canonical event accounting remains the projection owner; legacy paper books are migration inputs. Unsupported lifecycle facts and unresolved rows still block complete consolidation. | `tests/test_canonical_portfolio_ledger.py` |

The abbreviated blob IDs above are prefixes of the exact Git blob IDs recorded by the comparison. The only public-owner source change in this inventory is the forward runner; supporting execution modules also changed after PR #40. Existing tests are cited as current coverage pointers; they do not transfer a prior PR's CI result to `main` or prove end-to-end prospective trading.

## Remaining engineering gaps exposed by the comparison

- No live Kite quote adapter starts `ForwardSessionRunner` from the operator CLI.
- Legacy intraday and prospective runner paths still use distinct persistence/evidence lifecycles.
- Strategy identity, book-level capital allocation, and portfolio accounting remain partial as stated in the canonical contract registry.
- The reconciliation does not authorize merging unrelated open PRs or claim their checks passed.
